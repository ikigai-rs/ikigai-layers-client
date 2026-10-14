# ikigai-layers-client local gates.
#
# `just gates` runs scripts/gates.sh, the same script .github/workflows/ci.yml runs after
# installing: ruff check, ruff format --check, pytest, stopping at the first failure with
# that command's exit status. The devtools gates
# (`just -f ~/git-personal/ikigai-devtools/justfile gates`) find this recipe because the repo
# has no Cargo.toml.

# Lint, format check and tests, exactly as CI runs them.
gates *python:
    scripts/gates.sh {{python}}
