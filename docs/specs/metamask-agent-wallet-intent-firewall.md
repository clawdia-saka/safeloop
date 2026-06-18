# MetaMask Agent Wallet x SafeLoop Intent Firewall

Status: adaptation directive / product specification
Source: MetaMask Agent Wallet docs accessed 2026-06-18; TT/Poke design note 2026-06-18.

## Thesis

MetaMask Agent Wallet (MMAW) answers whether an agent is allowed to sign or send a transaction inside user-defined wallet limits. SafeLoop should sit one layer earlier and answer whether the agent's intended state trajectory should continue.

The product wedge is an Agent Transaction Firewall / Intent State Firewall that wraps `@metamask/agentic-sdk` and the `mm` CLI before signing. It should not replace MetaMask's transaction simulation, Blockaid threat scanning, Smart Transactions, or session-key authorization. It should add runtime semantic checks that static authorization cannot express:

- duplicate intent prevention before gas is spent;
- anti-churn / circular route detection across recent agent actions;
- NAV and utility invariants that include gas, slippage, and policy loss budget;
- fail-closed signing only after ledger, simulation, and invariant checks pass;
- agent cognition rollback / compensation when the chain result and agent memory diverge.

## Source-grounded assumptions

From the public MMAW docs:

- MMAW is a self-custodial agent wallet where the agent operates inside user-defined limits.
- Supported EVM transactions pass through a mandatory 3-step pipeline: transaction simulation, Blockaid threat scanning, and Smart Transactions where supported.
- The `mm` CLI exposes wallet operations such as auth, balances, transfers, signing/raw transactions, calldata decode, swaps/bridges, Hyperliquid, Polymarket, and market data.

SafeLoop's integration must treat those as the wallet boundary and place policy before the signature boundary. Do not claim MMAW lacks transaction simulation or malware scanning; the gap is trajectory-level intent consistency, not transaction decoding.

## Non-goals

- Do not take custody or replace MetaMask keys.
- Do not bypass MetaMask's confirmation, threat scanning, or Transaction Protection paths.
- Do not claim exact rollback for submitted onchain transactions.
- Do not store private keys, auth tokens, full calldata containing secrets, or raw wallet credentials.
- Do not present this as a production adapter until it has live-chain test coverage and explicit operator approval semantics.

## Architecture

```text
Agent / LLM planner
  -> canonical intent builder
  -> SafeLoop Intent Ledger + Pre-Sign Gate
  -> SafeLoop Trajectory Invariant Engine
  -> SafeLoop Fail-Closed Signing Gateway
  -> @metamask/agentic-sdk / mm CLI
  -> MetaMask simulation + Blockaid + Smart Transactions
  -> chain / protocol
  -> SafeLoop settlement watcher + agent cognition reconciler
```

## Layer 1: Intent Ledger + Pre-Sign Gate

Purpose: reject exact or semantically duplicate actions before signing.

Generate a deterministic idempotency key from canonical intent fields:

```yaml
idempotency:
  window_seconds: 900
  key_fields:
    - wallet
    - chain_id
    - intent_type
    - target_contract
    - canonical_args
    - rounded_amount_bucket
```

Required ledger fields:

```yaml
intent_ledger_record:
  schema_version: safeloop-agent-wallet-intent.v1
  intent_id: deterministic hash
  wallet: address or wallet alias
  chain_id: integer
  intent_type: swap | transfer | bridge | lend | perp | prediction_market | raw_transaction | unknown
  asset_in: token symbol/address or null
  asset_out: token symbol/address or null
  amount_in: string or null
  amount_out_min: string or null
  target_contract: address or protocol ref
  route: ordered protocol/token path
  expected_utility_usd: number or null
  max_total_loss_usd: number
  agent_reason: short bounded text
  status: planned | simulated | blocked | signed | submitted | confirmed | reverted | timed_out | compensated
  tx_hash: string or null
  evidence: paths/urls only, no secrets
```

If the same key is already `planned`, `simulated`, `signed`, `submitted`, or `confirmed` inside the dedupe window, block before signing.

## Layer 2: Trajectory Invariant Engine

Purpose: detect self-draining loops that are individually authorized but collectively irrational.

MVP policy:

```yaml
policy:
  anti_churn:
    lookback_minutes: 30
    reject_if_reverse_route: true
    min_expected_utility_usd: 5
    max_roundtrip_loss_bps: 30

  nav_guard:
    max_loss_bps: 50
    include_gas: true
    include_slippage: true
    oracle_sources:
      - quote
      - onchain_twap

  execution:
    simulation_required: true
    fail_closed: true
```

Required checks:

1. Reverse route guard
   - Reject if the ledger shows `asset_in=A, asset_out=B` followed soon by `asset_in=B, asset_out=A` unless the new intent has explicit positive expected utility above policy threshold.
   - Use the SafeLoop ledger, not only past N blocks, because failed, pending, cross-chain, and protocol-routed actions matter.

2. NAV guard
   - Estimate total loss as quoted slippage + gas budget + policy loss budget + known fees.
   - Reject if expected post-action NAV is below pre-action NAV minus allowed total loss.

3. Repeated loss guard
   - Reject sequences of small losses that individually pass but cumulatively exceed a policy budget.

4. Raw transaction guard
   - Raw calldata/signature requests must have decoded target/action evidence; unknown raw transactions default to manual review / fail-closed.

## Layer 3: Fail-Closed Signing Gateway

The gateway is the only component allowed to call `mm` signing/send commands or the SDK signing path.

Signing conditions:

```text
ledger_state_ok && simulation_ok && invariants_ok && operator_policy_ok
```

If any condition fails:

- do not sign;
- do not submit;
- mark the ledger record `blocked`;
- emit a SafeLoop explain artifact with the blocking invariant, source evidence, and operator next step.

## Layer 4: Settlement watcher + cognition rollback

Onchain state cannot be exactly rolled back. SafeLoop must roll back agent cognition and local workflow state, not the chain.

State machine:

```text
planned -> simulated -> blocked
planned -> simulated -> signed -> submitted -> confirmed
planned -> simulated -> signed -> submitted -> reverted
planned -> simulated -> signed -> submitted -> timed_out
```

Rules:

- Failure before `submitted`: restore Notion/Oxtane/agent task state to PreTx / Not Started.
- Failure after `submitted`: record the chain fact as final evidence and create a compensation/manual-review plan.
- Never describe a submitted onchain transaction as exactly rolled back.

## MVP implementation plan

### Phase 1: Intent Ledger + Pre-Sign Gate

- Add a SafeLoop wrapper around `mm`/SDK signing calls.
- Build canonical intent normalization for swap/transfer/bridge/raw tx.
- Persist the intent ledger as JSONL or SQLite.
- Enforce idempotency before signing.
- Add tests for duplicate intents and non-duplicate reverse intents.

### Phase 2: Trajectory Invariant Engine

- Add policy schema for anti-churn, NAV, and raw transaction guards.
- Evaluate recent ledger rows before signing.
- Add reverse ETH->USDC then USDC->ETH fixture.
- Add cumulative-loss fixture.
- Add raw transaction unknown-target fixture.

### Phase 3: Fail-Closed Gateway + Explain Artifacts

- Route all signing through one gateway entrypoint.
- Emit `intent-firewall-explain.json` and markdown summaries.
- Add operator packet integration so blocked intents show up in SafeLoop review packets.
- Add notification hooks only after local/offline tests pass.

## First GitHub issues to open

1. `feat(agent-wallet): add intent ledger schema and idempotency key builder`
2. `feat(agent-wallet): add trajectory invariant policy engine`
3. `feat(agent-wallet): add fail-closed mm signing gateway wrapper`
4. `test(agent-wallet): add reverse swap and cumulative loss fixtures`
5. `docs(agent-wallet): publish SafeLoop x MMAW integration guide`

## Review gates

- Unit tests for canonicalization, idempotency, and invariant decisions.
- Disposable local CLI smoke that never touches a real wallet.
- Secret scan: no wallet keys, recovery mnemonics, API keys, or raw auth headers.
- Qwen/adversarial review focused on bypasses: route splitting, amount bucketing evasion, cross-chain churn, raw calldata disguise, pending/reverted ledger desync.
- Documentation must preserve the boundary: MMAW handles wallet authorization/threat scanning; SafeLoop handles trajectory safety before signing.
