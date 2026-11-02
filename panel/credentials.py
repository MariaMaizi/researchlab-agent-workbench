"""Windows current-user DPAPI credential storage; never writes plaintext."""
import ctypes,os
from ctypes import wintypes
from pathlib import Path

KEY_FILE=Path(os.environ.get('RESEARCH_KEY_FILE',str(Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'ResearchLab/dify-key.dpapi')))

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def transform(raw,protect):
    if os.name!='nt':raise RuntimeError('加密凭据保存仅支持Windows；其他系统请使用后端环境变量。')
    buf=ctypes.create_string_buffer(raw);src=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.restype=wintypes.BOOL
    ok=fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out))
    if not ok:raise RuntimeError('Windows凭据加解密失败；请在当前Windows账户重新配置。')
    try:return ctypes.string_at(out.data,out.size)
    finally:
        kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree(ctypes.cast(out.data,ctypes.c_void_p))

def save(key):
    encrypted=transform(key.encode('utf-8'),True)
    KEY_FILE.parent.mkdir(parents=True,exist_ok=True)
    KEY_FILE.write_bytes(encrypted)

def load():
    if not KEY_FILE.exists():return ''
    try:return transform(KEY_FILE.read_bytes(),False).decode('utf-8')
    except Exception:return ''
