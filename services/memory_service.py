# services/memory_service.py

from memory import save_state as mem_save
from memory import load_state as mem_load

def save_user_data(data):
    return mem_save(data)

def load_user_data():
    return mem_load()
