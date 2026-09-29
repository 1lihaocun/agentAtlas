import hashlib
import json

import httpx

from atlas.core.atomic import atomic_write
from atlas.core.model_env import ModelEnvironment
from atlas.files.service import FileProblem


class TranslationService:
    def __init__(self, files, workspace, directory):
        self.files = files
        self.env = ModelEnvironment(workspace)
        self.directory = directory / "translations"

    def configuration(self):
        try:
            env = self.env.read()
        except ValueError as error:
            raise FileProblem(str(error)) from error
        provider = env.get("ATLAS_TRANSLATION_PROVIDER", "").strip() or "openai"
        model = env.get("ATLAS_TRANSLATION_MODEL", "").strip()
        defaults = {"openai": "https://api.openai.com/v1", "glm": "https://open.bigmodel.cn/api/paas/v4",
                    "zai": "https://open.bigmodel.cn/api/paas/v4", "openrouter": "https://openrouter.ai/api/v1",
                    "ollama": "http://127.0.0.1:11434/v1"}
        if provider not in defaults:
            raise FileProblem("请在项目 .env 中配置 ATLAS_TRANSLATION_PROVIDER：openai、glm、zai、openrouter 或 ollama")
        base = env.get("ATLAS_TRANSLATION_BASE_URL", "").strip() or defaults[provider]
        key = env.get("ATLAS_TRANSLATION_API_KEY", "").strip()
        if not model or (not key and provider != "ollama"):
            raise FileProblem("请在项目 .env 中填写 ATLAS_TRANSLATION_MODEL 和 ATLAS_TRANSLATION_API_KEY（Ollama 可不填密钥）")
        try:
            url = httpx.URL(base)
        except (httpx.InvalidURL, TypeError) as error:
            raise FileProblem("翻译服务地址格式不正确") from error
        if url.scheme not in ("http", "https") or (url.scheme == "http" and url.host not in {"127.0.0.1", "localhost", "::1"}):
            raise FileProblem("翻译服务需要使用 HTTPS 或本机 HTTP 地址")
        return {"provider": provider, "model": model, "base": base.rstrip("/"), "key": key}

    def translate(self, request):
        if not request.confirmCloud:
            raise FileProblem("请确认发送文件正文到配置的翻译服务")
        document = self.files.read(request.path)
        if not document["editable"]:
            raise FileProblem("此类文件不可发送至翻译服务", 403)
        if document["sha256"] != request.baseVersion:
            raise FileProblem("文件已经变化，请重新打开后翻译", 409)
        revision = self.env.revision()
        config = self.configuration()
        fingerprint = hashlib.sha256(json.dumps([document["sha256"], request.lang,
                                                  config["base"], config["model"]]).encode()).hexdigest()
        cache = self.directory / (fingerprint + ".json")
        if cache.is_file():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if not isinstance(cached, dict) or any(not isinstance(cached.get(key), str)
                        for key in ("path", "lang", "model", "provider", "translated", "source")):
                    raise ValueError("缓存结构无效")
            except (OSError, ValueError) as error:
                raise FileProblem(f"翻译缓存 {cache} 无效；请修复或移走缓存文件后重试") from error
            return cached
        chunks, current = [], ""
        for line in document["content"].splitlines(keepends=True):
            if len(current) + len(line) > 3000 and current:
                chunks.append(current)
                current = ""
            current += line
        chunks.append(current)
        outputs = []
        headers = {"Authorization": "Bearer " + (config["key"] or "ollama")}
        with httpx.Client(timeout=180, follow_redirects=False) as client:
            for chunk in chunks:
                try:
                    self.env.check_revision(revision)
                except ValueError as error:
                    raise FileProblem(str(error), 409) from error
                try:
                    response = client.post(config["base"] + "/chat/completions", headers=headers,
                        json={"model": config["model"], "temperature": 0.1, "messages": [
                            {"role": "system", "content": f"将 Markdown 翻译为{request.lang}，保留代码、链接、路径和结构，只输出译文。"},
                            {"role": "user", "content": chunk}]})
                except httpx.RequestError as error:
                    raise FileProblem("翻译服务连接失败或超时，请检查服务地址与网络", 502) from error
                if not response.is_success:
                    raise FileProblem(f"翻译服务请求失败（HTTP {response.status_code}）；请检查模型、凭证或服务额度", 502)
                try:
                    translated = response.json()["choices"][0]["message"]["content"]
                    if not isinstance(translated, str) or not translated.strip():
                        raise ValueError("译文必须是非空文本")
                except (ValueError, KeyError, IndexError, TypeError) as error:
                    raise FileProblem("翻译服务响应格式不正确，缺少有效译文", 502) from error
                outputs.append(translated)
        result = {"path": request.path, "lang": request.lang, "model": config["model"],
                  "provider": config["provider"], "translated": "\n".join(outputs), "source": document["content"]}
        atomic_write(cache, json.dumps(result, ensure_ascii=False).encode())
        return result
