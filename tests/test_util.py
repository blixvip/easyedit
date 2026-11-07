from easyedit.util import slugify


def test_long_project_title_never_ends_in_a_dash():
    assert slugify('a' * 59 + ' long title') == 'a' * 59
