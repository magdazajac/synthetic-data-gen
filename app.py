import io
import json
import re
import zipfile
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, inspect, text
from google import genai
from google.genai import types
from langfuse import Langfuse

# ---------------------------------------------------------
# PAGE CONFIGURATION
# ---------------------------------------------------------
st.set_page_config(
    page_title="Synthetic Data Generator & Text-to-SQL",
    page_icon="📊",
    layout="wide"
)

# ---------------------------------------------------------
# SECURE CREDENTIALS & SERVICE INITIALIZATION
# ---------------------------------------------------------
DB_URL = st.secrets.get(
    "DB_URL", 
    "postgresql://postgres:postgrespassword@localhost:5432/synthetic_db"
)
engine = create_engine(DB_URL)

langfuse = Langfuse(
    public_key=st.secrets.get("LANGFUSE_PUBLIC_KEY", "pk-dummy"),
    secret_key=st.secrets.get("LANGFUSE_SECRET_KEY", "sk-dummy"),
    host="https://cloud.langfuse.com"
)

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def make_zip_archive(tables_dict: dict) -> io.BytesIO:
    """Creates an in-memory ZIP archive containing individual CSV files for each table."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for table_name, rows in tables_dict.items():
            df = pd.DataFrame(rows)
            csv_bytes = df.to_csv(index=False).encode('utf-8')
            zip_file.writestr(f"{table_name}.csv", csv_bytes)
    buffer.seek(0)
    return buffer

def save_to_postgres(tables_data: dict) -> None:
    """Creates tables in PostgreSQL and populates them with generated data."""
    with engine.begin() as conn:
        for table_name, rows in tables_data.items():
            if rows:
                df = pd.DataFrame(rows)
                df.to_sql(table_name.lower(), con=conn, if_exists='replace', index=False)

def get_db_schema_string() -> str:
    """Extracts tables and column definitions from PostgreSQL database for LLM context."""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if not table_names:
        return "No tables found in the database."
    
    schema_info = []
    for table_name in table_names:
        columns = inspector.get_columns(table_name)
        col_defs = [f"{col['name']} ({col['type']})" for col in columns]
        schema_info.append(f"Table '{table_name}': " + ", ".join(col_defs))
    return "\n".join(schema_info)

def clean_sql_query(raw_response: str) -> str:
    """Cleans markdown code formatting from Gemini output to get raw SQL."""
    query = raw_response.strip()
    query = re.sub(r"^```sql\s*", "", query, flags=re.IGNORECASE)
    query = re.sub(r"^```\s*", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s*```$", "", query, flags=re.IGNORECASE)
    return query.strip()

# ---------------------------------------------------------
# SIDEBAR NAVIGATION
# ---------------------------------------------------------
st.sidebar.title("Navigation")
tab_selection = st.sidebar.radio("Select Mode:", ["Data Generation", "Talk to your data"])

# ---------------------------------------------------------
# TAB 1: DATA GENERATION
# ---------------------------------------------------------
if tab_selection == "Data Generation":
    st.title("📊 Synthetic Data Generation")

    uploaded_file = st.file_uploader("Upload DDL Schema File", type=["sql", "txt", "ddl"])
    user_prompt = st.text_area(
        "Additional Instructions (Prompt)", 
        placeholder="E.g., All books should belong to Sci-Fi, and member names should be realistic."
    )
    temperature = st.slider("Temperature (Creativity)", min_value=0.0, max_value=1.0, value=0.2, step=0.1)

    if st.button("Generate", type="primary"):
        if uploaded_file is None:
            st.error("Please upload a valid DDL schema file first!")
        else:
            with st.spinner("Gemini is analyzing schema and generating synthetic data..."):
                try:
                    ddl_schema = uploaded_file.read().decode("utf-8")
                    st.session_state['current_ddl'] = ddl_schema

                    client = genai.Client(
                        vertexai=True,
                        project='gd-gcp-gridu-genai',
                        location='us-central1'
                    )

                    prompt = f"""
You are a PostgreSQL database expert.
Analyze the following DDL schema:

{ddl_schema}

Additional user instructions:
{user_prompt if user_prompt else "No additional constraints."}

Generate 5 realistic rows of synthetic data for each table in the schema.
Strictly respect relational integrity, data types, primary keys, and foreign key references across tables.
Return the result ONLY as a valid JSON object where keys are table names and values are lists of row objects.
"""

                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=temperature,
                        ),
                    )

                    generated_json = json.loads(response.text)
                    st.session_state['generated_data'] = generated_json

                    # Persist generated data directly to PostgreSQL
                    save_to_postgres(generated_json)
                    st.success("Data successfully generated and stored in PostgreSQL database!")

                except Exception as e:
                    st.error(f"An error occurred during data generation: {e}")

    # Results Display, Export & Per-Table Feedback
    if 'generated_data' in st.session_state:
        st.write("---")
        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader("📋 Generated Data Preview")
        with col2:
            zip_buffer = make_zip_archive(st.session_state['generated_data'])
            st.download_button(
                label="📦 Download All (ZIP / CSV)",
                data=zip_buffer,
                file_name="generated_data.zip",
                mime="application/zip",
                use_container_width=True
            )

        tables = st.session_state['generated_data']
        table_names = list(tables.keys())
        tabs = st.tabs(table_names)
        
        for index, table_name in enumerate(table_names):
            with tabs[index]:
                rows = tables[table_name]
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True)
                
                # Individual Table Modification
                st.markdown(f"**Modify table `{table_name}`:**")
                edit_prompt = st.text_input(
                    f"Enter prompt instructions for '{table_name}':", 
                    key=f"prompt_{table_name}",
                    placeholder="E.g., Change all publication dates to 2024..."
                )
                
                if st.button(f"Submit changes for {table_name}", key=f"btn_{table_name}"):
                    if edit_prompt:
                        with st.spinner(f"Updating table '{table_name}'..."):
                            try:
                                client = genai.Client(
                                    vertexai=True,
                                    project='gd-gcp-gridu-genai',
                                    location='us-central1'
                                )
                                
                                modify_prompt = f"""
You are a database data editor.
Here is the existing generated data for table '{table_name}':
{json.dumps(rows, ensure_ascii=False)}

User modification request:
"{edit_prompt}"

Update the data in table '{table_name}' strictly according to the request.
Return the result ONLY as a valid JSON array of row objects for this SINGLE table.
"""
                                mod_response = client.models.generate_content(
                                    model='gemini-2.5-flash',
                                    contents=modify_prompt,
                                    config=types.GenerateContentConfig(
                                        response_mime_type="application/json",
                                        temperature=temperature,
                                    ),
                                )
                                
                                updated_table = json.loads(mod_response.text)
                                st.session_state['generated_data'][table_name] = updated_table
                                
                                # Synchronize modified dataset to PostgreSQL
                                save_to_postgres(st.session_state['generated_data'])
                                st.success(f"Table '{table_name}' successfully updated!")
                                st.rerun()

                            except Exception as e:
                                st.error(f"Error updating table '{table_name}': {e}")

# ---------------------------------------------------------
# TAB 2: TALK TO YOUR DATA (Text-to-SQL)
# ---------------------------------------------------------
elif tab_selection == "Talk to your data":
    st.title("💬 Talk to your data")
    st.markdown("Query your PostgreSQL database using natural language.")

    # Inspect current database schema
    try:
        current_schema = get_db_schema_string()
        with st.expander("🔍 View Active Database Schema Context"):
            st.text(current_schema)
    except Exception as e:
        st.warning(f"Could not connect to database or fetch schema: {e}")
        current_schema = "Schema unavailable."

    # User Query Input
    user_query = st.text_input(
        "Ask a question about your data:",
        placeholder="E.g., List all books along with their author names, or show members registered after 2020."
    )

    if st.button("Run Query", type="primary"):
        if not user_query:
            st.warning("Please enter a question first.")
        else:
            with st.spinner("Converting natural language to SQL..."):
                try:
                    client = genai.Client(
                        vertexai=True,
                        project='gd-gcp-gridu-genai',
                        location='us-central1'
                    )

                    sql_prompt = f"""
You are an expert PostgreSQL Text-to-SQL translator.
Given the following database schema:

{current_schema}

Convert this user natural language request into a valid, read-only SQL query:
"{user_query}"

Rules:
1. Return ONLY the raw SQL query. Do NOT wrap it in markdown code blocks or add text explanations.
2. Produce only standard SELECT statements (no INSERT, UPDATE, DELETE, or DROP).
3. Always match column and table names exactly as defined in the schema (case-sensitive if required).
"""

                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=sql_prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.0,
                        ),
                    )

                    generated_sql = clean_sql_query(response.text)

                    # Display Generated SQL Query
                    st.subheader("🤖 Generated SQL Query")
                    st.code(generated_sql, language="sql")

                    # Execute SQL query against PostgreSQL
                    with engine.connect() as conn:
                        result_df = pd.read_sql_query(text(generated_sql), con=conn)

                    # Display Query Result
                    st.subheader("📊 Query Results")
                    if result_df.empty:
                        st.info("The query executed successfully, but returned no rows.")
                    else:
                        st.dataframe(result_df, use_container_width=True)

                except Exception as e:
                    st.error(f"Error executing query: {e}")