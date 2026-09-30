"""Organization-owned ceilings for the allowlisted preference hierarchy."""
from __future__ import annotations


def entries(S, C, Column):
    from maverick.experience_preferences import DEFAULTS, PERSONAL_ONLY
    from maverick.persona import style_choices
    from maverick.styles import BUILTIN_STYLES

    common = {"group": "People & access", "reader": "maverick.experience_preferences",
              "subgroup": "Personal preference policy"}
    choices = tuple((key, key.replace("_", " ").capitalize())
                    for key in sorted(DEFAULTS.keys() - PERSONAL_ONLY))
    result = [S("personalization", "locked_fields", "Preferences controlled by organization policy",
                "Choose which presentation preferences members and workspace administrators cannot override. "
                "An unlisted preference can follow organization, workspace, personal and conversation defaults. "
                "Names, pins and notification windows always belong to the individual.",
                "multiselect", default=[], choices=choices, max_items=len(choices),
                weaker="change", warning="This changes which presentation choices members may override.",
                aliases="personal workspace organization inheritance locked preference policy", **common)]
    options = {
        "tone": style_choices(),
        "language": (("en", "English"), ("fr", "French"), ("de", "German"), ("ja", "Japanese"), ("ar", "Arabic"), ("zh", "Chinese")),
        "verbosity": (("brief", "Brief"), ("balanced", "Balanced"), ("detailed", "Detailed")),
        "output_style": (("", "Ordinary response style"), *tuple((key, key.capitalize()) for key in BUILTIN_STYLES), ("custom", "Required named style")),
        "font": (("default", "Default"), ("dyslexic", "Dyslexia-friendly")),
        "density": (("comfortable", "Comfortable"), ("compact", "Compact")),
        "text_size": (("standard", "Standard"), ("large", "Large"), ("larger", "Larger")),
    }
    for key, _label in choices:
        extra = {"choices_from": "preference_themes"} if key == "theme" else {"choices": options[key]} if key in options else {"max_length": 2000 if key == "custom_style" else 80}
        result.append(S("personalization.defaults", key, "Required " + key.replace("_", " "),
                        "Used only when this preference is controlled by organization policy above. "
                        "These choices change presentation, never tools, memory collection, retention or approval permissions.",
                        "select" if key in options or key == "theme" else "text", default=DEFAULTS[key],
                        **extra, **common))
    return result


REQUIRES_VALUE = {("personalization.defaults.output_style", "custom"): ("personalization.defaults.custom_style",)}
