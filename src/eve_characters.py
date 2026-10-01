import httpx
from eve_api import ESI, ESI_HEADERS  # , return_item_name
from eve_sso import get_access_token


def resolve_character(name: str) -> int:
    r = httpx.post(f"{ESI}/universe/ids/", json=[name], headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    hits = r.json().get("characters", [])

    if not hits:
        raise ValueError(f"No character exists for {name!r}")
    return hits[0]["id"]


def get_char_skills(character_id: int, access_token: str) -> dict:
    url = f"{ESI}/characters/{character_id}/skills/"
    headers = {**ESI_HEADERS, "Authorization": f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    skills = r.json()["skills"]

    # for s in skills:
    #     s["skill_name"] = return_item_name(s["skill_id"])

    return skills


if __name__ == "__main__":
    cid = resolve_character("Yatolila Saken")
    token = get_access_token(cid)
    skills = get_char_skills(cid, token)
    print(skills)
