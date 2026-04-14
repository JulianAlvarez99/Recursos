import os
import clr
import time
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

# Handler para consola (capturado por service_log.txt vía redirección)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

load_dotenv()

# Configuración .NET
os.chdir(os.path.dirname(os.path.abspath(__file__)))
dll_path = os.path.join(os.getcwd(), 'LibreHardwareMonitorLib.dll')
clr.AddReference(dll_path)
from LibreHardwareMonitor.Hardware import Computer

class TelemetryLogger:
    def __init__(self):
        self.table_name = os.getenv("CLIENT_TABLE_NAME")
        self.update_time = int(os.getenv("UPDATE_TIME", 10))
        self.conn = self._connect_to_db()
        self.pc = self._init_lhm()
        self.cache_hw = {}
        self.cache_sensor = {}
        self._load_metadata_cache()

    def _connect_to_db(self):
        return psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            port=os.getenv("DB_PORT"),
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=15,
            keepalives_interval=10,
            keepalives_count=5
        )

    def _init_lhm(self):
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

    def _load_metadata_cache(self):
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

            logger.info(f"Cache sincronizada con BD: {len(self.cache_sensor)} sensores conocidos.")
        except Exception as e:
            logger.error(f"Error cargando la cache de la base de datos: {e}")

    def _resolve_sensor_id(self, s_name: str, s_type: str) -> int | None:
        key = (s_name.upper(), s_type.upper())

        if key in self.cache_sensor:
            return self.cache_sensor[key]

        for pat_name, pat_type, pat_id in self.dynamic_patterns:
            if pat_type == s_type.upper() and fnmatch.fnmatch(s_name.upper(), pat_name):
                self.cache_sensor[key] = pat_id
                return pat_id

        DYNAMIC_REGEX = [
            (r'^CPU Core #\d+$',   'Temperature'),
            (r'^CPU Core #\d+$',   'Load'),
            (r'^CPU Core #\d+$',   'Power'),
            (r'^System Fan #\d+.*$', 'Fan'),
            (r'^CPU Fan.*$',       'Fan'),
            (r'^GPU Fan #\d+.*$',  'Fan'),
        ]
        
        for pattern, expected_type in DYNAMIC_REGEX:
            if s_type.lower() == expected_type.lower() and re.match(pattern, s_name, re.IGNORECASE):
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

    def _get_sensors_recursive(self, hardware_list):
        data = []
        for hw in hardware_list:
            hw.Update()
            for s in hw.Sensors:
                data.append((str(hw.HardwareType), str(hw.Name), s.Name, str(s.SensorType), s.Value))
            if list(hw.SubHardware):
                data.extend(self._get_sensors_recursive(hw.SubHardware))
        return data

    def _reconnect_db(self):
        """Intenta reconectar a la base de datos indefinidamente."""
        logger.warning("Conexion perdida con la base de datos. Intentando reconectar...")
        attempt = 0
        while True:
            attempt += 1
            try:
                # Cerrar conexión anterior de forma segura
                try:
                    if self.conn and not self.conn.closed:
                        self.conn.close()
                except Exception:
                    pass  # Ignorar errores al cerrar conexión rota
                
                self.conn = self._connect_to_db()
                self._load_metadata_cache()
                logger.info(f"Reconexión exitosa a la base de datos (intento #{attempt}).")
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError, OSError, ConnectionError) as e:
                logger.warning(f"Fallo al reconectar (intento #{attempt}): {e}. Reintentando en 60 segundos...")
                time.sleep(60)
            except Exception as e:
                logger.error(f"Error inesperado durante reconexión (intento #{attempt}): {e}. Reintentando en 60 segundos...")
                time.sleep(60)

    def run(self):
        LHM_TO_DB_HW = {
            "Cpu": "CPU", "GpuNvidia": "GPU", "GpuAti": "GPU",
            "Motherboard": "MOTHERBOARD", "SuperIO": "MOTHERBOARD",
            "Memory": "MEMORIA RAM", "Storage": "ALMACENAMIENTO"
        }

        logger.info(f"Iniciando captura de telemetria en tabla {self.table_name} (Intervalo: {self.update_time}s)...")
        
        fallos_consecutivos = 0

        try:
            while True:
                try:
                    now = datetime.now()
                    raw_sensors = self._get_sensors_recursive(self.pc.Hardware)
                    to_db = []

                    for lhm_hw_type, lhm_hw_name, s_name, s_type, s_val in raw_sensors:
                        if s_name == "Memory" and "Virtual Memory" in lhm_hw_name:
                            s_name = "Virtual Memory"

                        db_hw_type = LHM_TO_DB_HW.get(lhm_hw_type, "").upper()
                        h_id = self.cache_hw.get(db_hw_type)
                        s_id = self._resolve_sensor_id(s_name, s_type)

                        if h_id is not None and s_id is not None:
                            val = float(s_val) if s_val is not None else 0.0
                            to_db.append((now, h_id, s_id, lhm_hw_name, val))

                    if to_db:
                        with self.conn.cursor() as cur:
                            query = f"INSERT INTO {self.table_name} (timestamp, hardware_id, sensor_id, hardware_name, value) VALUES %s"
                            extras.execute_values(cur, query, to_db)
                            self.conn.commit()
                        
                        # Usamos DEBUG o INFO según qué tan ruidoso quieras el log. 
                        # Info está bien para saber que está vivo.
                        logger.info(f"OK: {len(to_db)} datos insertados correctamente.")

                    fallos_consecutivos = 0
                    time.sleep(self.update_time)

                except Exception as e:
                    fallos_consecutivos += 1
                    is_connection_error = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError))
                    
                    if is_connection_error:
                        logger.error(f"Error crítico de conexión detectado: {e}")
                        self._reconnect_db()
                        fallos_consecutivos = 0
                    else:
                        logger.error(f"Error inesperado durante captura (Fallo {fallos_consecutivos}/3): {e}", exc_info=True)
                        if fallos_consecutivos >= 3:
                            logger.error("Se superó el límite de 3 fallos consecutivos. Forzando cierre y reconexión de BD...")
                            self._reconnect_db()
                            fallos_consecutivos = 0
                        else:
                            time.sleep(self.update_time)

        except KeyboardInterrupt:
            logger.info("Deteniendo captura de telemetria por orden del usuario (Ctrl+C).")
        finally:
            self.pc.Close()
            if self.conn and not self.conn.closed:
                self.conn.close()
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