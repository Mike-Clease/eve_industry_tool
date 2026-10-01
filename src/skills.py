from eve_characters import resolve_character, get_skills
from eve_sso import get_access_token


# def get_skill_level(cid: int, skill_name: str) -> int:
#     """Returns a characters level in a given skill"""
#     pass


if __name__ == "__main__":
    cid = resolve_character("Stephen Schereau")
    token = get_access_token(cid)
    skills = get_skills(character_id=cid, access_token=token)
    print(skills["skills"][0])
    # #accounting = return_item_name("Accounting")
    # print(accounting)
    # print(
    #     *[
    #         x["trained_skill_level"]
    #         for x in skills["skills"]
    #         if x["skill_id"] == accounting
    #     ]
    # )
