# Proyecto de Captura de Métricas del Sistema

## Descripción

Este proyecto está diseñado para capturar y almacenar métricas de hardware y software de un sistema en una base de datos PostgreSQL. Utiliza `LibreHardwareMonitor` para recopilar datos de hardware y los scripts de Python para gestionar la base de datos y la captura de datos.

## Prerrequisitos

- Python 3.12+
- PostgreSQL
- .NET Framework (para `LibreHardwareMonitor`)

## Instalación

1.  **Clonar el repositorio:**
    ```bash
    git clone <URL-del-repositorio>
    cd <nombre-del-directorio>
    ```
2.  **Configurar las variables de entorno:**
    - Renombre el archivo `.env-example` a `.env`.
    - Edite el archivo `.env` con la configuración de su base de datos:
      ```
      DB_NAME=postgres
      DB_USER=postgres
      DB_PASSWORD=admin
      DB_HOST=localhost
      DB_PORT=5432
      ```

3. **Ejecutar como administrador instalador y seguir los pasos**
    install.bat

## Uso

### Base de Datos

1.  **Configuración inicial de la base de datos:**
    - Ejecute el siguiente script para crear la base de datos y las tablas necesarias:
      ```bash
      python db_setup.py
      ```

2.  **Inicializar las tablas maestras:**
    - Ejecute este script para poblar las tablas maestras con datos iniciales:
      ```bash
      python init_master_tables.py
      ```

### Captura de Datos

- Para iniciar la captura de datos, ejecute el script `run_capture.ps1` en una terminal de PowerShell:
  ```powershell
  .\run_capture.ps1
  ```

## Configuración del Servicio

Para configurar la captura de datos como un servicio en segundo plano, puede utilizar el script `service_setup.ps1`. Este script programará una tarea para que se ejecute periódicamente.

- **Ejecutar el script de configuración del servicio:**
  ```powershell
  .\service_setup.ps1
  ```

## Notas Adicionales

- **`LibreHardwareMonitor`**: Es una biblioteca de monitoreo de hardware de código abierto para sistemas Windows.
- **`Aga.Controls`**: Es una biblioteca de controles de interfaz de usuario utilizada en el proyecto.
- **`DiskInfoToolkit`**: Herramienta para obtener información detallada del disco.
- **`HidSharp`**: Biblioteca para la comunicación con dispositivos HID.
- **`OxyPlot`**: Biblioteca de trazado para la visualización de datos.
- **`Microsoft.Win32.TaskScheduler`**: Biblioteca para interactuar con el Programador de Tareas de Windows.
