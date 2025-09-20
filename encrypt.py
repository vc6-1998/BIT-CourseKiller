import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

def encrypt_password(password, salt):
    key_bytes = base64.b64decode(salt)
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    password_bytes = password.encode('utf-8')
    padded_password = pad(password_bytes, AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_password)
    encrypted_base64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    return encrypted_base64
