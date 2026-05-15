---
date: 2026-05-15
topic: dev-environment-setup
---

# Dev Environment Setup — hass-miner HA Integration

## Summary

Requirements for setting up a local development environment for the Solar Smart Miner HA integration: running a local HA instance via Dev Container (OrbStack recommended on macOS), connecting to a physical ASIC miner on the LAN, running the test suite without hardware, and syncing changes to a real HA instance for final validation.

---

## Problem Frame

Home Assistant integrations cannot be developed against the same HA instance that runs in production — that instance lives on a dedicated device (Raspberry Pi, NAS, or VM) on the home network. Writing and testing code requires a local HA instance that mirrors production closely enough to trust the results.

The default path — VS Code Dev Containers with Docker Desktop on macOS — has two under-documented failure points. First, Docker Desktop's Linux VM produces slow filesystem mounts that drag on the edit-test loop. Second, Docker Desktop's bridge networking silently breaks UDP-based device discovery, which matters for tools like `pyasic` that can discover miners via UDP broadcast. A developer who doesn't know to configure an explicit miner IP will hit an opaque failure at first run.

A third gap: when no physical miner is available, there is currently no specified mock that speaks the CGMiner RPC protocol. Without one, integration-level testing is hardware-dependent.

---

## Requirements

**Local HA instance**

- R1. The dev environment uses VS Code with the Dev Containers extension and a `.devcontainer.json` scaffolded from `ludeeus/integration_blueprint`
- R2. On macOS, the recommended container runtime is OrbStack (not Docker Desktop); OrbStack supports `--network=host` and native filesystem mount speeds
- R3. Running `scripts/develop` inside the container boots HA at `http://localhost:8123` with the integration pre-installed
- R4. Code edits on the host are reflected in the running HA instance via volume mount — no container rebuild required; HA must be restarted inside the container to pick up Python changes

**Miner connectivity**

- R5. The dev integration config uses the miner's explicit LAN IP address; miner auto-discovery (UDP broadcast) is not used in the dev environment
- R6. Outbound TCP connections from the container to LAN devices (miner API on port 4028, HTTP admin on port 80) work through Docker bridge networking on macOS without additional config — this covers all normal `pyasic` interaction when an explicit IP is provided
- R7. When OrbStack is the container runtime, `--network=host` is available if any dev scenario requires full LAN network parity

**Testing without hardware**

- R8. The unit test suite runs with `pytest-homeassistant-custom-component` and requires no running HA instance, no container, and no physical miner; all hass-miner entity state is mocked via `patch()`
- R9. For integration-level testing without a physical miner, a mock server that speaks the CGMiner RPC protocol must be available on the host (specific implementation deferred — see Outstanding Questions)
- R10. A mock server running on the host machine is reachable from inside the container at `host.docker.internal:<port>`

**Sync to real HA**

- R11. Final validation against a real HA instance is performed by copying `custom_components/solar_smart_miner/` to the production HA config directory via SCP or SSH; no automated sync tooling is required in v1

**Venv alternative**

- R12. A Python venv approach (HA installed directly via `pip install homeassistant`, run with `hass -c ./config`) is documented as an alternative for developers who prefer faster iteration without container overhead; Python version must match HA's constraint (3.12 as of 2026)

---

## Acceptance Examples

- AE1. **Covers R3, R4.** Given the devcontainer is running and `scripts/develop` has been executed, when a developer edits a `.py` file in `custom_components/solar_smart_miner/` on the host and restarts HA inside the container, the change is live at `http://localhost:8123` without rebuilding the container.

- AE2. **Covers R5, R6.** Given a miner at `192.168.1.50` is reachable from the developer's Mac, when the integration is configured with that IP in the dev HA instance, HA inside the container successfully reads the miner's sensor data via TCP.

- AE3. **Covers R5.** Given a developer uses `pyasic`'s discovery function (UDP broadcast) from inside a Docker Desktop container, the miner is not found; when the developer switches to an explicit IP, it is found. This confirms UDP discovery is not a supported dev env path.

- AE4. **Covers R8.** Given the devcontainer is not running and no physical miner is connected, when `pytest tests/` is executed on the host, all unit tests pass and no network connection is attempted.

- AE5. **Covers R10.** Given a mock CGMiner API server is running on the host at port 4028, when the dev integration config points to `host.docker.internal:4028`, HA inside the container reads the mock sensor data successfully.

---

## Success Criteria

- A developer with OrbStack and VS Code installed can reach a working HA dev instance at `localhost:8123` with the integration loaded in under 5 minutes from a fresh clone
- A developer without a physical miner can run all unit tests locally and interact with a mock miner via the container, covering the full decision cycle
- A change developed locally can be validated against a real HA + real miner instance by a single SCP command
- The UDP discovery failure mode is documented clearly enough that no developer wastes time debugging it

---

## Scope Boundaries

- CI/CD pipeline (GitHub Actions) — covered by integration_blueprint scaffold; not a dev env concern
- Windows or Linux dev environment variants — macOS only in v1; Linux is likely simpler (no VM layer) and can follow the same steps
- Automated dev→production sync tooling (rsync watch, HACS dev mode) — manual SCP is sufficient for v1
- Team / multi-developer reproducibility guarantees (devcontainer pinning, lockfiles) — solo developer assumed

---

## Key Decisions

- **OrbStack over Docker Desktop on macOS:** Docker Desktop runs containers in a Linux VM with slow filesystem mounts and broken `--network=host`; OrbStack resolves both; it is free for personal use and a drop-in Docker replacement
- **Explicit miner IP, not discovery:** `pyasic` UDP broadcast discovery does not traverse Docker bridge NAT; requiring an explicit IP in the dev config avoids a silent failure that is difficult to diagnose
- **venv documented as an alternative, not the default:** The integration_blueprint devcontainer is the community standard and U1 of the plan already includes `.devcontainer.json`; the venv path is a valid faster-iteration alternative for solo devs but is not the canonical path
- **Mock CGMiner server deferred:** No existing mock is specified; this is an outstanding question that must be resolved before a developer can do integration testing without hardware

---

## Dependencies / Assumptions

- OrbStack (free for personal use) or Docker Desktop installed on the developer's Mac
- VS Code with the Dev Containers extension
- Physical miner reachable from the Mac (same LAN) OR a mock CGMiner API server for hardware-free dev
- Python 3.12 available on host if using the venv alternative (must match HA's constraint)
- `ludeeus/integration_blueprint` used as the devcontainer scaffold base (established in plan U1)

---

## Outstanding Questions

### Resolve Before Planning

*(none)*

### Deferred to Planning

- **[Affects R9] [Needs research]** What existing tool or Docker image can mock the CGMiner RPC API protocol that `pyasic` speaks? Options to investigate: a lightweight Python stub, an existing open-source CGMiner simulator, or a `pyasic` test fixture that can be exposed as a network endpoint
