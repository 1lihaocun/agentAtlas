"""平台注册表：一个平台一个子目录，启动时自动发现。

新增平台 = 在本目录新建一个子包（目录 + ``__init__.py``），定义

    PLATFORM = Platform(id="名称", ...)

注册表把各平台的目录、裁剪例外、类别提示与记忆读取规则聚合给
catalog / scan / memory_scope / memory_metadata 使用；本文件只做
发现与聚合，不写任何平台细节。所有导入保持 try/except 双形式，
兼容包内运行（atlas.xxx）与脚本直跑（顶层 xxx）。
"""
import importlib
import pkgutil

try:
    from .registry import Platform
except ImportError:  # platforms/ 目录被当作脚本根时。
    from registry import Platform

_CACHE = None


def platforms():
    """发现并导入全部平台子包，返回 {id: Platform}，结果进程内缓存。"""
    global _CACHE
    if _CACHE is None:
        found = {}
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
            if not info.ispkg:
                continue
            module = importlib.import_module("." + info.name, __package__)
            spec = getattr(module, "PLATFORM", None)
            if spec is None:
                raise ValueError("平台子包 %s 缺少 PLATFORM 声明" % info.name)
            if spec.id in found:
                raise ValueError("平台 id 重复：%s" % spec.id)
            found[spec.id] = spec
        _CACHE = found
    return _CACHE


def by_id(platform_id):
    return platforms().get(platform_id)


def user_roots():
    """[(HOME 相对目录, 平台 id)]，与旧 catalog._USER_ROOTS 同形。"""
    return [(name, spec.id) for spec in platforms().values() for name in spec.user_roots]


def project_roots():
    """[(项目根相对目录, 平台 id)]，与旧 catalog._PROJECT_ROOTS 同形。"""
    return [(name, spec.id) for spec in platforms().values() for name in spec.project_roots]


def home_globs():
    """[(HOME glob 模式, 平台 id)]。"""
    return [(pattern, spec.id) for spec in platforms().values() for pattern in spec.home_glob]


def scan_root_names():
    """参与指令地图扫描的平台目录名（HOME 相对）。"""
    names = []
    for spec in platforms().values():
        if not spec.instruction_scan:
            continue
        names.extend(spec.scan_roots if spec.scan_roots is not None else spec.user_roots)
    return names


def instruction_platform():
    """{文件名小写: 平台 id}，与旧 catalog._INSTRUCTION_PLATFORM 同形。"""
    mapping = {}
    for spec in platforms().values():
        for name in spec.instruction_names:
            mapping[name.lower()] = spec.id
    return mapping


def bootstrap_instructions(platform_id):
    spec = by_id(platform_id)
    return set(spec.bootstrap_instructions) if spec else set()


def prune_rules(platform_id):
    """(任意层额外裁剪, 第一层裁剪, 第一层豁免) 三个集合。"""
    spec = by_id(platform_id)
    if spec is None:
        return set(), set(), set()
    return (set(spec.pruned_dirs), set(spec.first_level_pruned),
            set(spec.first_level_allowed))


def category_dirs(platform_id):
    spec = by_id(platform_id)
    return spec.category_dirs if spec else {}


def database_platforms():
    return {spec.id for spec in platforms().values() if spec.session_database}


def memory_resolver(platform_id):
    spec = by_id(platform_id)
    return spec.resolve_memory if spec else None


def extension_declaration(platform_id, profile, tail):
    spec = by_id(platform_id)
    if spec is None or spec.extension_declaration is None:
        return False
    return bool(spec.extension_declaration(profile, tail))
