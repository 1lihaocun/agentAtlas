from pathlib import Path

import atlas
from atlas.app.factory import create_app
from atlas.core.config import AppConfig
from atlas.guides.service import FileGuide
from atlas.platforms import user_roots

source = Path(atlas.__file__).resolve()
if '/backend/src/' in source.as_posix():
    raise RuntimeError('验证需要导入安装包中的模块')
guide = FileGuide()
library = guide.library()
if not guide.path.is_file() or not library['types']:
    raise RuntimeError('安装包缺少有效的文件类型说明库')
if not user_roots():
    raise RuntimeError('安装包缺少平台注册信息')
application = create_app(AppConfig(workspace=Path.cwd(), refresh_on_start=False))
if application.openapi()['info']['version'] != atlas.__version__:
    raise RuntimeError('OpenAPI 版本与安装包版本不一致')
print(f'安装包导入成功；文件类型 {len(library["types"])} 项；OpenAPI 生成成功。')
