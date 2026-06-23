"""Pure-function shell test for _port_html.

Calls mai.web.app._port_html directly (no server, no auth, no DB) and
asserts the three wiring markers that Task 4 requires are present.
"""

from mai.web.app import _port_html


def test_port_shell_wiring():
    html = _port_html("testuser", is_maintainer=False)
    assert 'id="f-core"' in html
    assert 'src="/static/portboard.js"' in html
    assert 'id="port-board"' in html
