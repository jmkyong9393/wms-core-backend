"""FastAPI OpenAPI 스키마에서 API 명세서(Markdown)를 생성한다.

손으로 관리하던 명세서가 구현과 어긋나는 문제(v1.7.0.1 기준 16개 vs 구현 73개)를
해소하기 위해, 코드에서 직접 뽑아낸다. 재생성 명령은 문서 상단에 적어 둔다.
"""

import io
import sys
from collections import OrderedDict
from datetime import date

sys.path.insert(0, ".")

from app.main import app  # noqa: E402

METHOD_ORDER = ["get", "post", "put", "patch", "delete"]


def resolve_ref(ref: str, schemas: dict) -> dict:
    return schemas.get(ref.rsplit("/", 1)[-1], {})


def type_of(schema: dict, schemas: dict, depth: int = 0) -> str:
    """스키마를 한 줄 타입 표기로 축약한다."""
    if depth > 3 or not isinstance(schema, dict):
        return "object"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            parts = [type_of(s, schemas, depth + 1) for s in schema[key]]
            parts = [p for p in parts if p != "null"]
            return " | ".join(dict.fromkeys(parts)) or "any"
    t = schema.get("type")
    if t == "array":
        return f"{type_of(schema.get('items', {}), schemas, depth + 1)}[]"
    if schema.get("enum"):
        return " | ".join(f"`{v}`" for v in schema["enum"])
    return t or "object"


def main() -> None:
    spec = app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})

    # 태그별로 묶되, 태그가 없는 경로는 "기타"로 모은다.
    by_tag: "OrderedDict[str, list]" = OrderedDict()
    total = 0
    for path, item in sorted(spec.get("paths", {}).items()):
        for method in METHOD_ORDER:
            op = item.get(method)
            if not op:
                continue
            total += 1
            tag = (op.get("tags") or ["기타"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))

    out = []
    out.append("# API 명세서 (자동 생성)")
    out.append("")
    out.append(
        "> **이 문서는 손으로 고치지 않습니다.** FastAPI가 만드는 OpenAPI 스키마에서 "
        "생성하므로, 코드를 바꾸면 아래 명령으로 다시 뽑아 주세요."
    )
    out.append(">")
    out.append("> ```bash")
    out.append("> uv run python scripts/generate_api_spec.py")
    out.append("> ```")
    out.append(">")
    out.append(
        "> 실행 중인 서버에서는 `http://localhost:8080/docs`(Swagger UI)로도 볼 수 있습니다."
    )
    out.append("")
    out.append(f"- 생성일: {date.today().isoformat()}")
    out.append(f"- 엔드포인트: **{total}개** / 태그 {len(by_tag)}개")
    out.append("")

    # 목차
    out.append("## 목차")
    out.append("")
    for tag, ops in by_tag.items():
        anchor = tag.lower().replace(" ", "-")
        out.append(f"- [{tag}](#{anchor}) ({len(ops)}개)")
    out.append("")

    for tag, ops in by_tag.items():
        out.append(f"## {tag}")
        out.append("")
        out.append("| Method | Path | 설명 | 인증 |")
        out.append("|---|---|---|---|")
        for method, path, op in ops:
            summary = (op.get("summary") or "").replace("|", "\\|")
            auth = "필요" if op.get("security") or "admin" in path else "-"
            out.append(f"| `{method}` | `{path}` | {summary} | {auth} |")
        out.append("")

        for method, path, op in ops:
            out.append(f"### `{method}` {path}")
            out.append("")
            if op.get("summary"):
                out.append(f"**{op['summary']}**")
                out.append("")
            if op.get("description"):
                out.append(op["description"].strip())
                out.append("")

            params = op.get("parameters") or []
            if params:
                out.append("**요청 파라미터**")
                out.append("")
                out.append("| 이름 | 위치 | 필수 | 타입 | 설명 |")
                out.append("|---|---|---|---|---|")
                for p in params:
                    req = "O" if p.get("required") else "-"
                    desc = (p.get("description") or "").replace("|", "\\|").replace("\n", " ")
                    out.append(
                        f"| `{p['name']}` | {p['in']} | {req} | "
                        f"{type_of(p.get('schema', {}), schemas)} | {desc} |"
                    )
                out.append("")

            body = op.get("requestBody")
            if body:
                for media, content in (body.get("content") or {}).items():
                    name = type_of(content.get("schema", {}), schemas)
                    out.append(f"**요청 본문** (`{media}`): `{name}`")
                    out.append("")
                    fields = schemas.get(name)
                    if fields and fields.get("properties"):
                        required = set(fields.get("required") or [])
                        out.append("| 필드 | 필수 | 타입 | 설명 |")
                        out.append("|---|---|---|---|")
                        for fname, fschema in fields["properties"].items():
                            desc = (
                                (fschema.get("description") or "")
                                .replace("|", "\\|")
                                .replace("\n", " ")
                            )
                            out.append(
                                f"| `{fname}` | {'O' if fname in required else '-'} | "
                                f"{type_of(fschema, schemas)} | {desc} |"
                            )
                        out.append("")

            responses = op.get("responses") or {}
            if responses:
                out.append("**응답**")
                out.append("")
                out.append("| 코드 | 설명 | 본문 |")
                out.append("|---|---|---|")
                for code, resp in sorted(responses.items()):
                    desc = (resp.get("description") or "").replace("|", "\\|")
                    schema_name = "-"
                    for content in (resp.get("content") or {}).values():
                        schema_name = f"`{type_of(content.get('schema', {}), schemas)}`"
                        break
                    out.append(f"| {code} | {desc} | {schema_name} |")
                out.append("")

    io.open("docs/API_Specification.md", "w", encoding="utf-8", newline="\n").write(
        "\n".join(out) + "\n"
    )
    print(f"docs/API_Specification.md written: {total} endpoints, {len(by_tag)} tags")


if __name__ == "__main__":
    main()
