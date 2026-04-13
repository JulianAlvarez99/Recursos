import os
import psycopg2
from dotenv import load_dotenv

def setup_database():
    load_dotenv()
    
    db_host = os.getenv("DB_HOST")
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASS")
    db_port = os.getenv("DB_PORT", "5432")
    table_name = os.getenv("CLIENT_TABLE_NAME")

    if not all([db_host, db_name, db_user, db_pass, table_name]):
        print("❌ ERROR: Faltan variables en el archivo .env (Revisa DB_HOST, DB_NAME, DB_USER, DB_PASS, CLIENT_TABLE_NAME)")
        return False

    print(f"🔄 Conectando a la base de datos {db_name} en {db_host}...")
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
        print("✅ Conexión establecida.")

        # 1. Verificar tablas Maestras
        print("🔍 Verificando tablas maestras (Sensor, Componente)...")
        cur.execute("SELECT 1 FROM pg_tables WHERE tablename='sensor' OR tablename='Sensor';")
        if not cur.fetchone():
            print("❌ ERROR: La tabla 'sensor' no existe. Ejecuta primero init_master_tables.py.")
            return False

        cur.execute("SELECT 1 FROM pg_tables WHERE tablename='componente' OR tablename='Componente';")
        if not cur.fetchone():
            print("❌ ERROR: La tabla 'componente' no existe. Ejecuta primero init_master_tables.py.")
            return False
        
        print("✅ Tablas maestras encontradas.")

        # 2. Crear tabla de telemetría del cliente como Hypertable de TimescaleDB
        print(f"🔨 Asegurando existencia de la tabla {table_name} (Timescale Hypertable)...")
        
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            timestamp TIMESTAMP NOT NULL,
            hardware_id INT NOT NULL,
            sensor_id INT NOT NULL,
            hardware_name TEXT,
            value FLOAT
        );
        """
        
        try:
            cur.execute(create_table_query)
            print(f"✅ Tabla {table_name} base creada.")
            
            # Convertir en hypertable (TimescaleDB)
            cur.execute(f"SELECT count(*) FROM _timescaledb_catalog.hypertable WHERE table_name = '{table_name}';")
            if cur.fetchone()[0] == 0:
                print(f"🔄 Convirtiendo {table_name} en Hypertable (TimescaleDB)...")
                cur.execute(f"SELECT create_hypertable('{table_name}', 'timestamp', if_not_exists => TRUE);")
                print(f"✅ ¡Hypertable creada con éxito!")
            else:
                print(f"ℹ️ La tabla {table_name} ya es una Hypertable.")

            # --- CONFIGURACIÓN DE COMPRESIÓN ---
            try:
                # 1. Habilitar la compresión en la hypertable
                # Segmentamos por hardware_id y sensor_id (búsquedas rápidas) y ordenamos por timestamp
                print("🔄 Configurando parámetros de compresión...")
                cur.execute(f"""
                    ALTER TABLE {table_name} SET (
                        timescaledb.compress,
                        timescaledb.compress_segmentby = 'hardware_id, sensor_id',
                        timescaledb.compress_orderby = 'timestamp DESC'
                    );
                """)
                print("✅ Compresión habilitada en la Hypertable.")

                # 2. Eliminar política de retención antigua si existiera (Para evitar conflictos)
                try:
                    cur.execute(f"SELECT remove_retention_policy('{table_name}', if_exists => TRUE);")
                    print("ℹ️ Se eliminó la política de retención anterior (si existía).")
                except Exception:
                    pass # Si no existe, no hacemos nada

                # 3. Añadir la política de compresión (datos mayores a 7 días)
                print("🔄 Aplicando política de compresión automática (> 7 días)...")
                cur.execute(f"SELECT add_compression_policy('{table_name}', INTERVAL '7 days', if_not_exists => TRUE);")
                print("✅ Política de compresión de 7 días aplicada exitosamente.")

            except Exception as ce:
                # A veces da error si la compresión ya estaba habilitada. 
                # TimescaleDB suele lanzar una excepción si intentas hacer ALTER a algo ya comprimido sin usar parámetros específicos.
                if "already" in str(ce).lower():
                     print("ℹ️ La política de compresión o los parámetros ya estaban configurados.")
                else:
                     print(f"⚠️ Error configurando compresión: {ce}")
            # -----------------------------------------

            # --- CONFIGURACIÓN DE TRIGGERS DE AUDITORÍA ---
            try:
                print("🔄 Configurando los triggers de auditoría para el nuevo cliente...")
                cur.execute(f"DROP TRIGGER IF EXISTS tr_verificar_umbrales ON {table_name};")
                cur.execute(f"""
                    CREATE TRIGGER tr_verificar_umbrales
                    AFTER INSERT ON {table_name}
                    FOR EACH ROW EXECUTE FUNCTION fn_auditar_umbrales();
                """)
                
                cur.execute(f"DROP TRIGGER IF EXISTS tr_reconexion ON {table_name};")
                cur.execute(f"""
                    CREATE TRIGGER tr_reconexion
                    BEFORE INSERT ON {table_name}
                    FOR EACH ROW EXECUTE FUNCTION fn_registrar_reconexion();
                """)
                print("✅ Triggers de auditoría asociados exitosamente al cliente.")
            except Exception as e_trig:
                print(f"⚠️ Error asociando los triggers de auditoría (¿Ya ejecutaste procedures.py?): {e_trig}")
            # ----------------------------------------------

        except Exception as e:
            print(f"❌ ERROR al configurar la tabla: {e}")
            return False

        cur.close()
        conn.close()
        print("\n🚀 ¡Configuración de TimescaleDB completada con soporte de compresión!")
        return True

    except Exception as e:
        print(f"❌ ERROR de conexión: {e}")
        return False

if __name__ == "__main__":
    if setup_database():
        exit(0)
    else:
        exit(1)