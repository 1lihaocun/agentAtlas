"""共享的语义与上下文构造器：memory_scope 与各平台 resolver 共用。"""
from copy import deepcopy

_LEVELS = {"user": "用户级", "profile": "配置档级", "workspace": "工作区级",
           "project": "项目级", "directory": "目录级", "session": "会话级", "unknown": "范围未知"}
_MODES = {"automatic": "条件自动加载", "on_demand": "按需读取", "retrieval": "检索召回",
          "manual": "官方规则未记录", "manual_label": "人工读取", "none": "不直接加载", "unknown": "加载方式未知"}
_MODES = {"automatic": "条件自动加载", "on duplicated _MODES = {"automatic": "条件自动加载", "on_demand": "按需读取", "retrieval": "检索召回",
          "manual": "人工读取", "none": "不直接加载", "on duplicated _MODES = {"automatic": "content-corruption-sentinel-8f3a2b", "on duplicated _MODES = {"automatic": "content-corruption-sentinel-8f3a2b"