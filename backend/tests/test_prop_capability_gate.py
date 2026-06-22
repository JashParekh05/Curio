"""Property-based test for the capability gate.

# Feature: alternative-content-streams, Property 2: Capability gate admits exactly the declared operations

``supports_capability`` and ``gate_invocation`` together enforce that the
Ingestion_Pipeline invokes through a provider only the operations that provider
has declared as Provider_Capabilities (Req 1.3). An attempt to invoke an
undeclared operation is rejected with the ``'unsupported_operation'`` error label
and touches no persisted Source_Item state (Req 1.4). The same gate is what makes
a provider lacking a capability skip the corresponding operation (Req 5.5).

For any capability set (including the empty set) and any operation:
  - ``supports_capability`` is True exactly when the op is in the set;
  - ``gate_invocation`` returns ``None`` exactly when the op is in the set, else
    ``'unsupported_operation'``.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 1.3, 1.4, 5.5
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import (
    UNSUPPORTED_OPERATION,
    ProviderCapability,
    gate_invocation,
    supports_capability,
)

# An operation is any one of the three declared Provider_Capabilities.
_ops = st.sampled_from(list(ProviderCapability))

# A capability set is any subset of the capabilities, including the empty set.
_capability_sets = st.sets(_ops).map(frozenset)


class TestCapabilityGate:
    @settings(max_examples=100)
    @given(capabilities=_capability_sets, op=_ops)
    def test_supports_capability_iff_in_set(self, capabilities, op):
        # supports_capability is True exactly when op is among the declared set.
        assert supports_capability(capabilities, op) is (op in capabilities)

    @settings(max_examples=100)
    @given(capabilities=_capability_sets, op=_ops)
    def test_gate_returns_none_exactly_when_supported(self, capabilities, op):
        result = gate_invocation(capabilities, op)
        if op in capabilities:
            # Permitted: the gate admits the invocation with no error.
            assert result is None
        else:
            # Rejected: the specific 'unsupported_operation' label is returned.
            assert result == UNSUPPORTED_OPERATION

    @settings(max_examples=100)
    @given(capabilities=_capability_sets, op=_ops)
    def test_gate_and_supports_agree(self, capabilities, op):
        # The gate admits (None) exactly when supports_capability is True.
        assert (gate_invocation(capabilities, op) is None) is (
            supports_capability(capabilities, op)
        )
