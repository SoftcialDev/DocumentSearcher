import os
import re
import tempfile
import shutil
from datetime import datetime, timedelta
from openai import OpenAI
from azure.storage.blob import BlobServiceClient, BlobSasPermissions, generate_blob_sas

def generate_code():
    client = OpenAI(api_key="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")

    assistant = client.beta.assistants.create(
        name="Excel Generator",
        instructions="You are a Python assistant. Your tasks is to answer with python code and nothing else, no explanations no breakthrough no example usages no recomendations, exclusively source code of the requests and nothing else",
        tools=[{"type" : "code_interpreter"}],
        model="gpt-4-turbo"
    )

    thread = client.beta.threads.create()

    message = client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=f"""

        Create a Python method that receives two parameters: 
        1. A list of dictionaries representing rows of data, where each dictionary has the same keys and corresponds to a row.
        2. A string for the file name (without extension).

        The method should convert the list into an Excel file using the keys as column headers. 
        All required imports must be inside the method. 
        Output only the source code.
    """
    )

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id
    )

    import time

    while run.status not in ["completed", "failed"]:
        print(f"Run status {run.status}")
        time.sleep(2)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)


    if (run.status == "faiiled"):
        print("Assistant failed to process the request")
        exit()

    messages = client.beta.threads.messages.list(thread_id=thread.id)

    code = None

    for message in messages.data:
        if message.role == "assistant":
            for content in message.content:
                if content.type == "text":
                    code = content.text.value

    return code

def clean_code_block(code_str):
    return re.sub(r"```(?:python)?\n?|```", "", code_str).strip()

def get_first_function_name(local_vars):
    return next((k for k, v in local_vars.items() if callable(v)), None)

def run_code_with_input(code_str, *args):
    local_vars = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        # We'll use this as the base file name
        base_name = "output"
        output_file_no_ext = os.path.join(tmpdir, base_name)
        
        # Compile code and find function
        exec(code_str, globals(), local_vars)
        func_name = get_first_function_name(local_vars)
        if func_name is None:
            raise ValueError("No function found in the provided code.")
        
        # Append file name *without extension*
        full_args = (*args, output_file_no_ext)
        local_vars[func_name](*full_args)

        # Now we know the actual generated file path
        actual_output_file = output_file_no_ext + ".xlsx"

        # Copy to working dir
        final_path = os.path.abspath("output.xlsx")
        shutil.copyfile(actual_output_file, final_path)
        return final_path
    
def upload_to_azure(file_path):
    account_name = 'softcialstaging'
    account_key = 'CkuBUMLt4o0MJDVNJHCN8HhJqIs44XKYxIKTJweZHnlfcyDLCvDxd3uMYtAHfA15pI4o5t4Z5Pq4+AStddAwyg=='
    container_name = 'excelfiles'
    blob_name = os.path.basename(file_path)

    blob_service_client = BlobServiceClient(account_url=f"https://{account_name}.blob.core.windows.net", credential=account_key)
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)

    with open(file_path, "rb") as f:
        blob_client.upload_blob(f, overwrite=True)

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(hours=1)
    )

    sas_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"
    return sas_url

if __name__ == "__main__":
    code = clean_code_block(generate_code())
    data = [{"a" : "a", "b" : "b", "c" : "c"}]
    file_path = run_code_with_input(code, data)
    url = upload_to_azure(file_path)
    print(url)
