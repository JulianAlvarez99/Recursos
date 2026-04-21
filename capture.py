import os
import clr
import time
import threading
from queue import Queue, Empty
import psycopg2
from psycopg2 import extras
from datetime import datetime
from dotenv import load_dotenv
import re
import fnmatch
import logging
from logging.handlers import RotatingFileHandler

# --- CONFIGURACIÓN DEL LOGGER ---
# Crea un log que se guarda en el mismo directorio del script
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'telemetry_capture.log')

# Formato: [Fecha Hora] - NIVEL - Mensaje
log_formatter = logging.Formatter('[%(asctime)s] - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Configurar el logger principal
logger = logging.getLogger('HardwareTelemetry')
logger.setLevel(logging.INFO)

# Handler para escribir en el archivo de log (con rotación: 5MB max, 3 backups)
file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

load_dotenv()

# Configuración .NET
os.chdir(os.path.dirname(os.path.abspath(__file__)))
dll_path = os.path.join(os.getcwd(), 'LibreHardwareMonitorLib.dll')
clr.AddReference(dll_path)
from LibreHardwareMonitor.Hardware import Computer

# Mapeo constante de tipos de hardware LHM → tipos de la BD
LHM_TO_DB_HW = {
    "Cpu": "CPU", "GpuNvidia": "GPU", "GpuAti": "GPU",
    "Motherboard": "MOTHERBOARD", "SuperIO": "MOTHERBOARD",
    "Memory": "MEMORIA RAM", "Storage": "ALMACENAMIENTO"
}

# Patrones regex para auto-registro de sensores dinámicos (pre-compilados)
DYNAMIC_REGEX = [
    (re.compile(r'^CPU Core #\d+$', re.IGNORECASE),     'temperature'),
    (re.compile(r'^CPU Core #\d+$', re.IGNORECASE),     'load'),
    (re.compile(r'^CPU Core #\d+$', re.IGNORECASE),     'power'),
    (re.compile(r'^System Fan #\d+.*$', re.IGNORECASE), 'fan'),
    (re.compile(r'^CPU Fan.*$', re.IGNORECASE),          'fan'),
    (re.compile(r'^GPU Fan #\d+.*$', re.IGNORECASE),     'fan'),
]


class TelemetryLogger:
    def __init__(self):
        self.table_name = os.getenv("CLIENT_TABLE_NAME")
        self.update_time = int(os.getenv("UPDATE_TIME", 10))
        self.conn = self._connect_to_db()
        self.pc = self._init_lhm()
        self.cache_hw = {}
        self.cache_sensor = {}
        self.dynamic_patterns = []

        # 1. Cargar metadata de la BD (componentes y sensores)
        self._load_metadata_cache()

        # 2. Descubrir todos los sensores del hardware y pre-resolver sus IDs
        self.sensor_plan = self._build_sensor_plan()

        # 3. Cola thread-safe para comunicación Productor → Consumidor
        #    maxsize=5 para evitar acumulación excesiva si la BD se atrasa
        self._data_queue = Queue(maxsize=5)

        # 4. Evento para señalizar apagado limpio de hilos
        self._stop_event = threading.Event()

    # ─── Conexión y configuración ──────────────────────────────────────

    def _connect_to_db(self):
        host = os.getenv("DB_HOST")
        db   = os.getenv("DB_NAME")
        logger.info(f"Conectando a la base de datos '{db}' en {host}...")
        conn = psycopg2.connect(
            host=host,
            database=db,
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            port=os.getenv("DB_PORT"),
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=15,
            keepalives_interval=10,
            keepalives_count=5
        )
        logger.info(f"Conexión establecida con '{db}' en {host}.")
        return conn

    def _init_lhm(self):
        logger.info("Inicializando LibreHardwareMonitor...")
        c = Computer()
        c.IsCpuEnabled = True
        c.IsGpuEnabled = True
        c.IsMemoryEnabled = True
        c.IsMotherboardEnabled = True
        c.IsControllerEnabled = True
        c.IsStorageEnabled = True
        c.IsPsuEnabled = True
        c.Open()
        return c

    # ─── Carga de metadata y resolución de sensores ────────────────────

    def _load_metadata_cache(self):
        """Carga la tabla Componente y Sensor de la BD en memoria."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT hardware_id, hardware_type FROM Componente")
                self.cache_hw = {row[1].upper(): row[0] for row in cur.fetchall()}

                cur.execute("SELECT sensor_id, sensor_name, sensor_type FROM Sensor")
                rows = cur.fetchall()

            self.cache_sensor = {}
            self.dynamic_patterns = []

            for sensor_id, sensor_name, sensor_type in rows:
                key = (sensor_name.upper(), sensor_type.upper())
                self.cache_sensor[key] = sensor_id
                if '%' in sensor_name:
                    self.dynamic_patterns.append((sensor_name.upper(), sensor_type.upper(), sensor_id))

            logger.info(
                f"Cache cargada: {len(self.cache_hw)} componentes, "
                f"{len(self.cache_sensor)} sensores "
                f"({len(self.dynamic_patterns)} con patrón wildcard)."
            )
        except Exception as e:
            logger.error(f"Error cargando la cache de la base de datos: {e}", exc_info=True)

    def _resolve_sensor_id(self, s_name: str, s_type: str) -> int | None:
        """Resuelve el sensor_id. Se ejecuta una sola vez por sensor en _build_sensor_plan."""
        key = (s_name.upper(), s_type.upper())

        # 1. Coincidencia exacta en cache
        if key in self.cache_sensor:
            return self.cache_sensor[key]

        # 2. Coincidencia por patrón wildcard (%) de la BD
        for pat_name, pat_type, pat_id in self.dynamic_patterns:
            if pat_type == s_type.upper() and fnmatch.fnmatch(s_name.upper(), pat_name):
                self.cache_sensor[key] = pat_id
                return pat_id

        # 3. Auto-registro si coincide con un patrón regex conocido
        s_type_lower = s_type.lower()
        for compiled_re, expected_type in DYNAMIC_REGEX:
            if s_type_lower == expected_type and compiled_re.match(s_name):
                try:
                    with self.conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO Sensor (sensor_name, sensor_type) VALUES (%s, %s) "
                            "ON CONFLICT DO NOTHING RETURNING sensor_id",
                            (s_name, s_type)
                        )
                        res = cur.fetchone()
                        if res:
                            new_id = res[0]
                        else:
                            cur.execute("SELECT sensor_id FROM Sensor WHERE sensor_name=%s AND sensor_type=%s", (s_name, s_type))
                            new_id = cur.fetchone()[0]

                        self.conn.commit()
                    self.cache_sensor[key] = new_id
                    logger.info(f"Sensor dinámico auto-registrado: {s_name} ({s_type}) → id={new_id}")
                    return new_id
                except Exception as e:
                    self.conn.rollback()
                    logger.error(f"Error auto-registrando sensor '{s_name}': {e}")
                    return None

        return None

    def _collect_all_hardware(self):
        """Recorre iterativamente todo el árbol de hardware y devuelve una lista plana
        de (hw_object, lhm_type_str, db_hw_type_upper)."""
        result = []
        stack = list(self.pc.Hardware)
        while stack:
            hw = stack.pop()
            lhm_type = str(hw.HardwareType)
            db_hw_type = LHM_TO_DB_HW.get(lhm_type, "").upper()
            result.append((hw, lhm_type, db_hw_type))
            sub = list(hw.SubHardware)
            if sub:
                stack.extend(sub)
        return result

    def _build_sensor_plan(self):
        """Se ejecuta una sola vez al inicio. Descubre todos los sensores del hardware,
        resuelve sus IDs, y devuelve una lista de tuplas listas para el loop:
        [(hw_object, hardware_name, hardware_id, sensor_object, sensor_id), ...]

        Los sensores que no resuelven ID se descartan aquí y no se vuelven a evaluar."""
        logger.info("Construyendo plan de sensores (resolución única de IDs)...")

        hw_list = self._collect_all_hardware()
        for hw, _, _ in hw_list:
            hw.Update()

        plan = []
        skipped = 0

        for hw, lhm_hw_type, db_hw_type in hw_list:
            h_id = self.cache_hw.get(db_hw_type)
            if h_id is None:
                continue

            hw_name = str(hw.Name)

            for s in hw.Sensors:
                s_name = s.Name
                s_type = str(s.SensorType)

                # Rename "Memory" → "Virtual Memory" para sensores de RAM virtual
                resolved_name = "Virtual Memory" if (s_name == "Memory" and "Virtual Memory" in hw_name) else s_name

                s_id = self._resolve_sensor_id(resolved_name, s_type)
                if s_id is None:
                    skipped += 1
                    logger.debug(f"Sensor descartado (sin ID en BD): '{resolved_name}' tipo '{s_type}' en '{hw_name}'")
                    continue

                plan.append((hw, hw_name, h_id, s, s_id))

        logger.info(f"Plan de sensores construido: {len(plan)} sensores activos, {skipped} descartados sin ID en BD.")
        return plan

    # ─── Reconexión ────────────────────────────────────────────────────

    def _reconnect_db(self):
        """Intenta reconectar a la base de datos indefinidamente."""
        logger.critical("Conexión perdida con la base de datos. Iniciando ciclo de reconexión...")
        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1
            try:
                try:
                    if self.conn and not self.conn.closed:
                        self.conn.close()
                        logger.debug("Conexión anterior cerrada correctamente antes de reconectar.")
                except Exception:
                    pass

                self.conn = self._connect_to_db()
                self._load_metadata_cache()
                logger.info(f"Reconexión exitosa a la base de datos (intento #{attempt}).")
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError, OSError, ConnectionError) as e:
                logger.warning(f"Reconexión fallida (intento #{attempt}): {e}. Reintentando en 60s...")
                self._stop_event.wait(timeout=60)
            except Exception as e:
                logger.error(f"Error inesperado durante reconexión (intento #{attempt}): {e}. Reintentando en 60s...", exc_info=True)
                self._stop_event.wait(timeout=60)

    # ─── Hilo Productor: captura de sensores ───────────────────────────

    def _producer_loop(self):
        """Hilo dedicado a la lectura de hardware.
        Captura timestamp → actualiza hardware → lee valores → pone batch en la cola."""
        logger.info("Hilo productor de sensores iniciado.")

        # Pre-calcular el set de objetos hw únicos para evitar recálculo cada ciclo
        unique_hw = []
        seen = set()
        for hw, _, _, _, _ in self.sensor_plan:
            obj_id = id(hw)
            if obj_id not in seen:
                unique_hw.append(hw)
                seen.add(obj_id)

        while not self._stop_event.is_set():
            try:
                # 1. Capturar timestamp al inicio de la lectura
                now = datetime.now()

                # 2. Actualizar cada hardware una sola vez
                for hw in unique_hw:
                    hw.Update()

                # 3. Leer valores del plan pre-resuelto
                batch = []
                for _, hw_name, h_id, sensor, s_id in self.sensor_plan:
                    val = float(sensor.Value) if sensor.Value is not None else 0.0
                    batch.append((now, h_id, s_id, hw_name, val))

                # 4. Enviar batch a la cola (bloquea si la cola está llena)
                if batch:
                    self._data_queue.put(batch)
                else:
                    logger.warning("Productor generó un batch vacío; ningún sensor activo reportó valores.")

            except Exception as e:
                logger.error(f"Error en hilo productor durante la captura de sensores: {e}", exc_info=True)

            # Esperar el intervalo, pero interrumpible por stop_event
            self._stop_event.wait(timeout=self.update_time)

        logger.info("Hilo productor finalizado.")

    # ─── Hilo Principal (Consumidor): inserción en BD ──────────────────

    def _consumer_loop(self):
        """Loop principal: toma batches de la cola e inserta en la BD.
        queue.get() bloquea hasta que el productor deposita datos → sincronización natural."""
        insert_query = f"INSERT INTO {self.table_name} (timestamp, hardware_id, sensor_id, hardware_name, value) VALUES %s"
        fallos_consecutivos = 0

        while not self._stop_event.is_set():
            # --- Esperar datos del productor (con timeout para poder chequear stop_event) ---
            try:
                batch = self._data_queue.get(timeout=self.update_time * 2)
            except Empty:
                logger.debug("Consumidor: timeout esperando datos del productor (posible retraso o inactividad).")
                continue

            # --- Insertar en la BD ---
            try:
                with self.conn.cursor() as cur:
                    extras.execute_values(cur, insert_query, batch)
                    self.conn.commit()

                logger.info(f"Inserción OK: {len(batch)} registros insertados en '{self.table_name}'.")
                fallos_consecutivos = 0

            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                logger.critical(f"Error de conexión con la BD durante inserción: {e}")
                self._reconnect_db()
                fallos_consecutivos = 0
                # Reintentar insertar el batch perdido tras reconectar
                logger.info("Reintentando inserción del batch pendiente tras reconexión...")
                try:
                    with self.conn.cursor() as cur:
                        extras.execute_values(cur, insert_query, batch)
                        self.conn.commit()
                    logger.info(f"Batch pendiente re-insertado correctamente ({len(batch)} registros).")
                except Exception as retry_e:
                    logger.error(f"Fallo al re-insertar batch pendiente tras reconexión: {retry_e}", exc_info=True)

            except Exception as e:
                fallos_consecutivos += 1
                logger.error(f"Error inesperado durante inserción (fallo {fallos_consecutivos}/3): {e}", exc_info=True)
                if fallos_consecutivos >= 3:
                    logger.critical("Límite de 3 fallos consecutivos alcanzado. Forzando reconexión a la BD...")
                    self._reconnect_db()
                    fallos_consecutivos = 0

    # ─── Entry point ───────────────────────────────────────────────────

    def run(self):
        logger.info(f"Iniciando captura de telemetria en tabla {self.table_name} (Intervalo: {self.update_time}s)...")

        # Lanzar hilo productor como daemon (muere automáticamente si el principal termina)
        producer = threading.Thread(
            target=self._producer_loop,
            name="SensorProducer",
            daemon=True
        )
        producer.start()

        try:
            # El hilo principal actúa como consumidor (inserción en BD)
            self._consumer_loop()
        except KeyboardInterrupt:
            logger.info("Deteniendo captura de telemetria por orden del usuario (Ctrl+C).")
        finally:
            # Señalizar apagado limpio
            logger.info("Señalizando apagado a los hilos...")
            self._stop_event.set()
            producer.join(timeout=5)
            logger.info("Cerrando LibreHardwareMonitor...")
            self.pc.Close()
            if self.conn and not self.conn.closed:
                self.conn.close()
                logger.info("Conexión con la base de datos cerrada.")
            logger.info("Recursos liberados. Script finalizado.")


if __name__ == "__main__":
    app = TelemetryLogger()
    app.run()


# Mother 
# Vcore => Voltaje que recibe el procesador.
# VRM MOS => Sensor de los reguladores de voltaje
# CPU Fan => Velocidad de ventilador de CPU
# System Fan #X => Velocidad de ventilador adicional (Pueden ser varios)

# CPU
# CORE (Tctl/Tdie) => Sensor de temperatura principal
# Package => Consumo total en Watts
# CPU Total => Porcentaje de uso global
# CPU Core #X => Porcentaje de uso de procesador logico

# Almacenamiento
# Composite Temperature => Temperatura general del reporte SMART
# Used Space => Porcentaje de espacio utilizado
# Read Activity => Porcentaje de lectura 
# Write Activity => Porcentaje de escritura
# Total Activity => Porcentaje de actividad total

# GPU
# GPU Package => Consumo de GPU en Watts
# GPU Core => Temperatura del procesador grafico
# GPU Memory Junction => Temperatura mas elevada de los modulos de memoria
# GPU Fan #X => Velocidad de los ventiladores 

# RAM
# DIMM => Sensores térmicos
# Physical Memory => Porcentaje de ocupación de memoria física
# Virtual Memory => Porcentaje de ocupación de memoria virtual