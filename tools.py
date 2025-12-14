import os

def run_command(cmd: str):
    result = os.system(cmd)
    return result
