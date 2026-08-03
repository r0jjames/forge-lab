import pytest

import get_license

LABEL = "10 user Bamboo Data Center license, expires in 24 hours"


def page(body):
    """The timebomb page embeds markdown in JSON, so newlines arrive escaped."""
    return f'{{"content": "...{LABEL}\\n\\n{body}"}}'


def test_extracts_a_single_line_key():
    html = page("```bash\\nAAAABBBBCCCC==\\n```")
    assert get_license.extract_license_key(html, LABEL) == "AAAABBBBCCCC=="


def test_joins_a_wrapped_key():
    html = page("```bash\\nAAAA\\nBBBB\\nCCCC\\n```")
    assert get_license.extract_license_key(html, LABEL) == "AAAABBBBCCCC"


def test_picks_the_block_after_the_label_not_an_earlier_one():
    html = f'{{"x": "```bash\\nWRONGKEY\\n```...{LABEL}\\n```bash\\nRIGHTKEY\\n```"}}'
    assert get_license.extract_license_key(html, LABEL) == "RIGHTKEY"


def test_missing_label_exits_with_its_own_code():
    with pytest.raises(get_license.LicenseError) as err:
        get_license.extract_license_key("nothing here", LABEL)
    assert err.value.code == get_license.EXIT_LABEL_NOT_FOUND


def test_unparseable_block_exits_with_its_own_code():
    with pytest.raises(get_license.LicenseError) as err:
        get_license.extract_license_key(page("no code fence here"), LABEL)
    assert err.value.code == get_license.EXIT_UNPARSEABLE


def test_non_key_text_is_rejected():
    with pytest.raises(get_license.LicenseError) as err:
        get_license.extract_license_key(page("```bash\\nnot a key!\\n```"), LABEL)
    assert err.value.code == get_license.EXIT_NOT_A_KEY
