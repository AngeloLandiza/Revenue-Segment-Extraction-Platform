from __future__ import annotations


class JsonExtractionError(ValueError):
    pass


def extract_json_object(response_text: str) -> str:
    stripped_text = response_text.strip()
    if not stripped_text:
        raise JsonExtractionError("Provider returned an empty response")

    fenced_json = _extract_fenced_json(stripped_text)
    if fenced_json is not None:
        return fenced_json

    start_index = stripped_text.find("{")
    if start_index < 0:
        raise JsonExtractionError("Provider response did not contain a JSON object")

    end_index = _find_matching_object_end(stripped_text, start_index)
    if end_index is None:
        raise JsonExtractionError("Provider response contained an incomplete JSON object")
    return stripped_text[start_index : end_index + 1]


def _extract_fenced_json(response_text: str) -> str | None:
    fence_start = response_text.find("```")
    if fence_start < 0:
        return None

    content_start = response_text.find("\n", fence_start)
    if content_start < 0:
        return None

    fence_end = response_text.find("```", content_start + 1)
    if fence_end < 0:
        return None

    fenced_content = response_text[content_start + 1 : fence_end].strip()
    if not fenced_content:
        return None
    return fenced_content


def _find_matching_object_end(text: str, start_index: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start_index, len(text)):
        character = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index

    return None
