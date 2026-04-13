import os
import psycopg2
from dotenv import load_dotenv

def init_tables():
    load_dotenv()
    
    db_host = os.getenv("DB_HOST")
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASS")
    db_port = os.getenv("DB_PORT", "5432")

    if not all([db_host, db_name, db_user, db_pass]):
        print("❌ ERROR: No se encontraron credenciales completas en el .env")
        return

    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_pass,
            port=db_port
        )
        conn.autocommit = True
        cur = conn.cursor()
        print(f"✅ Conectado a {db_host}. Iniciando configuración de TimescaleDB...")

        # 0. Habilitar extensión TimescaleDB
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
            print("🚀 Extensión TimescaleDB verificada/habilitada.")
        except Exception as e:
            print(f"⚠️ Nota sobre TimescaleDB: {e}")

        # 1. Crear tabla Componente
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Componente (
                hardware_id INT PRIMARY KEY,
                hardware_type TEXT UNIQUE NOT NULL
            );
        """)

        componentes = [
            (0, "CPU"),
            (1, "GPU"),
            (2, "MOTHERBOARD"),
            (3, "MEMORIA RAM"),
            (4, "FUENTE"),
            (5, "ALMACENAMIENTO")
        ]

        for cid, ctype in componentes:
            cur.execute("""
                INSERT INTO Componente (hardware_id, hardware_type) 
                VALUES (%s, %s) 
                ON CONFLICT (hardware_id) DO UPDATE SET hardware_type = EXCLUDED.hardware_type;
            """, (cid, ctype))
        print("✅ Tabla 'Componente' inicializada.")

        # 2. Crear tabla Sensor
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Sensor (
                sensor_id INT PRIMARY KEY,
                sensor_name TEXT NOT NULL,
                sensor_type TEXT NOT NULL,
                UNIQUE(sensor_name, sensor_type)
            );
        """)

        sensores = [
            (1, "Memory", "Load"),
            (2, "Virtual Memory", "Load"),
            (3, "Temperature", "Temperature"),
            (4, "Used Space", "Load"),
            (5, "Read Activity", "Load"),
            (6, "Write Activity", "Load"),
            (7, "Total Activity", "Load"),
            (8, "Life", "Level"),
            (9, "GPU Package", "Power"),
            (10, "GPU Core", "Temperature"),
            (11, "GPU Memory Junction", "Temperature"),
            (12, "Vcore", "Voltage"),
            (13, "+12V", "Voltage"),
            (14, "+5V", "Voltage"),
            (15, "+3.3V", "Voltage"),
            (16, "VRM MOS", "Temperature"),
            (17, "CPU Fan", "Fan"),
            (18, "Core (Tctl/Tdie)", "Temperature"),
            (19, "Package", "Power"),
            (20, "CPU Total", "Load"),
            (21, "System Fan #1", "Fan"),
            (22, "System Fan #2", "Fan"),
            (23, "System Fan #3", "Fan"),
            (24, "System Fan #4", "Fan"),
            (25, "CPU Core #1", "Load"),
            (26, "CPU Core #2", "Load"),
            (27, "CPU Core #3", "Load"),
            (28, "CPU Core #4", "Load"),
            (29, "CPU Core #5", "Load"),
            (30, "CPU Core #6", "Load"),
            (31, "CPU Core #7", "Load"),
            (32, "CPU Core #8", "Load"),
            (33, "CPU Core #9", "Load"),
            (34, "CPU Core #10", "Load"),
            (35, "CPU Core #11", "Load"),
            (36, "CPU Core #12", "Load"),
            (37, "CPU Core #13", "Load"),
            (38, "CPU Core #14", "Load"),
            (39, "CPU Core #15", "Load"),
            (40, "CPU Core #16", "Load"),
            (41, "GPU Core", "Load"),
            (42, "System Fan #6 / Pump", "Fan"),
            (43, "System Fan #5 / Pump", "Fan")
        ]

        for sid, sname, stype in sensores:
            cur.execute("""
                INSERT INTO Sensor (sensor_id, sensor_name, sensor_type) 
                VALUES (%s, %s, %s) 
                ON CONFLICT (sensor_id) DO UPDATE 
                SET sensor_name = EXCLUDED.sensor_name, sensor_type = EXCLUDED.sensor_type;
            """, (sid, sname, stype))
        print("✅ Tabla 'Sensor' inicializada.")

        cur.close()
        conn.close()
        print("\n🚀 Proceso completado exitosamente.")

    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    init_tables()
