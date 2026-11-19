# Reproducibility and integrity

1. Use a source checkout/archive. The accepted science environment is Python
   3.10.19, NumPy 2.2.6, SciPy 1.15.3. Other science profiles/environment tuples
   remain unvalidated and may be rejected. The backend is tested separately on
   Python 3.11 with `requirements-backend.txt`.
2. Install only missing requirements. No virtual environment, node_modules,
   downloaded compiler, executable or old experiment output is distributed.
3. Run the README examples with new paths. Seeds are explicit. Observation
   generation and evaluation are separate; dataset identity is checked before
   scoring. Do not use the evaluation-only reference to choose baseline inputs.
4. Preserve input/config/engine/source SHA, task identity and artifact index.
   `SOURCE_PROVENANCE.json` protects original scientific bytes. Root standalone
   and chapter-7 code sets remain separate; never mix their same-named modules.
5. Results are synthetic and uncalibrated. Torch-dependent legacy methods need
   their own environment and protocol; importing their source is not acceptance.
6. Default scientific worker and `--mode official` remain blocked pending a full
   registered-ownership capacity check below 200,000,000,000 bytes. Small smoke
   preparation is an explicit maintenance operation, not a quota expansion.

The original repository history is not rewritten. This release branch selects a
small payload; pushing an existing-history branch can still send old objects.
Use the prepared standalone source repository/archive after public-payload checks.
No private machine path or Codex conversation is required by the minimum example.
Runtime-generated records may naturally include the user's own interpreter path.

Legacy large optimization runs are excluded from the release payload, not
silently deleted or relabelled out of local ownership. Any retirement/archiving
decision must preserve necessary downstream sources or explicitly retire their
consumers. External archives require an actual independent destination, integrity
manifest and restoration test before local deletion. XML replacement and original
rollback retirement remain separately approved operations.
