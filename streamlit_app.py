import streamlit as st
import requests
import base64
import re



# Cabecera con autenticación en base64 para llamadas a la API de Azure
headers = {
    'Authorization': 'Basic ' + base64.b64encode(f":{token}".encode()).decode()
}

# Configuración de la página de Streamlit
st.set_page_config(page_title="Buscador de Archivos Azure", layout="wide")
st.title("Generador de DDL desde Markdown en Azure Repos")

# Entradas de usuario para configurar la búsqueda
keyword = st.text_input("🔍 Palabra clave para buscar archivos:")
database_name = st.text_input("🏷️ Nombre de la base de datos:", value="MiBaseDatos")
only_md = st.checkbox("📄 Buscar solo archivos `.md`", value=True)
folder_filter = st.text_input("📁 Filtrar por carpeta (opcional):", placeholder="/docs/")

# Función para obtener todos los repositorios del proyecto
@st.cache_data
def get_all_repos():
    url = f"https://dev.azure.com/{organization}/{project}/_apis/git/repositories?api-version=7.1-preview.1"
    response = requests.get(url, headers=headers)
    return response.json().get('value', []) if response.status_code == 200 else []

# Función para obtener todos los archivos de un repositorio
@st.cache_data
def buscar_archivos_en_repo(repo_id):
    url = f'https://dev.azure.com/{organization}/{project}/_apis/git/repositories/{repo_id}/items?scopePath=/&recursionLevel=Full&api-version=7.1-preview.1'
    response = requests.get(url, headers=headers)
    return response.json().get('value', []) if response.status_code == 200 else []

# Se obtienen todos los repositorios disponibles y se mapean nombre -> id
all_repos = get_all_repos()
repo_options = {f"{r['name']}": r['id'] for r in all_repos}

# Inicializa el estado de selección de repositorios si no está en la sesión
if 'selected_repos' not in st.session_state:
    st.session_state.selected_repos = list(repo_options.keys())

# Se muestra una interfaz para seleccionar repos
col1, col2 = st.columns([4, 1])
with col1:
    selected_repo_names = st.multiselect("📦 Selecciona repos donde buscar:", list(repo_options.keys()), default=st.session_state.selected_repos)

# Guarda los repos seleccionados en el estado
if selected_repo_names:
    st.session_state.selected_repos = selected_repo_names

matched_files = []  # Lista de archivos que coinciden con la búsqueda

# Si hay palabra clave y repos seleccionados, se realiza la búsqueda
if keyword and selected_repo_names:
    st.info("🔍 Buscando archivos...")
    for repo_name in selected_repo_names:
        repo_id = repo_options[repo_name]
        archivos = buscar_archivos_en_repo(repo_id)
        for f in archivos:
            path = f['path'].lower()
            # Filtra los archivos que cumplen con los criterios del usuario
            if (
                keyword.lower() in path and
                not f.get('isFolder', False) and
                (not only_md or path.endswith('.md')) and
                (not folder_filter or folder_filter.lower() in path)
            ):
                matched_files.append({
                    "repo_id": repo_id,
                    "repo_name": repo_name,
                    "path": f['path']
                })

# Si se encontraron archivos que coinciden con la búsqueda
if matched_files:
    opciones = [f"{f['repo_name']} - {f['path']}" for f in matched_files]
    seleccion = st.selectbox("📁 Selecciona un archivo:", opciones)

    # Botón para cargar el archivo seleccionado
    if st.button("📂 Cargar archivo"):
        seleccionado = matched_files[opciones.index(seleccion)]

        # URL para obtener el contenido bruto del archivo
        raw_url = f"https://dev.azure.com/{organization}/{project}/_apis/git/repositories/{seleccionado['repo_id']}/items?path={seleccionado['path']}&api-version=7.1-preview.1"
        content_resp = requests.get(raw_url, headers=headers)

        if content_resp.status_code == 200:
            file_content = content_resp.text

            # Muestra el contenido markdown
            st.subheader("📝 Contenido del archivo Markdown:")
            st.markdown(f"```markdown\n{file_content}\n```")

            # Intenta extraer el nombre de la tabla del contenido
            tabla_match = re.search(r'###\s*(\w+)', file_content)
            if not tabla_match:
                tabla_match = re.search(r'Tabla\s*:\s*(\w+)', file_content, re.IGNORECASE)
            nombre_tabla = tabla_match.group(1).upper() if tabla_match else "MiTabla"

            st.success(f"Nombre de la tabla detectado: `{nombre_tabla}`")

            # Intenta extraer una descripción debajo del nombre de la tabla
            desc_match = re.search(rf"{nombre_tabla}.*\n(.+)", file_content, re.IGNORECASE)
            desc_text = desc_match.group(1).strip() if desc_match else ""

            columnas = []

            # Extrae las filas de la tabla markdown (formato de columnas esperadas)
            rows = re.findall(r"\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|", file_content)
            for _, col, tipo, comentario in rows:
                columnas.append({
                    'nombre': col.strip().upper(),
                    'tipo': tipo.strip(),
                    'comentario': comentario.strip()
                })

            # Si se encontraron columnas, se genera el DDL
            if columnas:
                ddl = f"CREATE MULTISET TABLE {database_name}.{nombre_tabla}, FALLBACK,\n"
                ddl += "NO BEFORE JOURNAL,\nNO AFTER JOURNAL,\n"
                ddl += "CHECKSUM = DEFAULT,\nDEFAULT MERGEBLOCKRATIO,\nMAP = TD_MAP1\n(\n"
                ddl += ",\n".join([f"    {col['nombre']} {col['tipo']}" for col in columnas])
                ddl += "\n);\n\n"

                # Comentarios para tabla y columnas
                ddl += f"COMMENT ON TABLE {database_name}.{nombre_tabla} IS '{nombre_tabla}: {desc_text}';\n"
                for col in columnas:
                    ddl += f"COMMENT ON COLUMN {database_name}.{nombre_tabla}.{col['nombre']} IS '{col['nombre']}: {col['comentario']}';\n"

                # Muestra el código generado y permite descargarlo
                st.subheader("🛠️ Código DDL generado:")
                st.code(ddl, language='sql')

                ddl_file = ddl.encode('utf-8')
                st.download_button(
                    label="💾 Descargar DDL como .sql",
                    data=ddl_file,
                    file_name=f"{nombre_tabla}_DDL.sql",
                    mime="text/sql"
                )
            else:
                st.warning("⚠️ No se encontraron columnas en formato de tabla.")
        else:
            st.error("❌ Error al descargar el archivo.")
elif keyword:
    st.warning("⚠️ No se encontraron archivos con esa palabra clave en los repos seleccionados.")
