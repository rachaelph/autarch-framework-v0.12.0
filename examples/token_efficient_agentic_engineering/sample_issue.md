# Engineering request

Add JWT authentication to the reporting API. Validate issuer, audience, signature,
expiry, and not-before claims. Keep authorization deny-by-default and separate from
authentication. Add unit and API integration tests, security-safe telemetry,
operator documentation, and a key-rotation runbook. Existing public error shapes
must remain backward compatible.

## Acceptance criteria

- Invalid or absent credentials return the stable unauthorized response.
- Rejected requests do not invoke protected business services.
- No raw token or secret is logged.
- Tests cover positive, negative, and authorization-boundary cases.
- Documentation explains configuration and key rotation.
