"""板端当前用户的私有密钥配置；不位于工程或导出目录。"""
import json
import os
from pathlib import Path
import tempfile


def validate_key(value):
    value = value.strip()
    if value and not all(33 <= ord(char) <= 126 for char in value):
        raise ValueError('密钥中含空白或非法字符，请重新复制')
    return value


class CredentialStore:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder is not None else Path.home()/'.config/rock5c-ppt'
        self.path = self.folder/'credentials.json'

    def load(self):
        if not self.path.exists():
            return ''
        if self.folder.is_symlink() or self.path.is_symlink():
            raise ValueError('密钥配置不能使用符号链接')
        if os.name == 'posix':
            if self.path.stat().st_uid != os.getuid():
                raise ValueError('密钥配置不属于当前用户')
            self.folder.chmod(0o700)
            self.path.chmod(0o600)
        try:
            value = json.loads(self.path.read_text(encoding='utf-8')).get('deepseek_api_key', '')
        except (ValueError, AttributeError):
            raise ValueError('密钥配置格式错误，请在设置中重新保存') from None
        if not isinstance(value, str):
            raise ValueError('密钥配置格式错误，请在设置中重新保存')
        return validate_key(value)

    def save(self, value):
        value = validate_key(value)
        if self.folder.is_symlink() or self.path.is_symlink():
            raise ValueError('密钥配置不能使用符号链接')
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'posix':
            self.folder.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix='.credentials-', dir=str(self.folder))
        try:
            if os.name == 'posix':
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'deepseek_api_key': value}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
