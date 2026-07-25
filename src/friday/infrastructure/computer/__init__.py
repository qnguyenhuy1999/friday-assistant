"""Computer-use substrate: value objects, the ComputerDriver port, and the
driver adapters that talk to a real desktop.

Everything in this package sits behind ComputerToolGateway. No module outside
friday.infrastructure.computer and friday.infrastructure.tools may import it —
see tests/architecture for the enforced boundary.
"""

from __future__ import annotations
