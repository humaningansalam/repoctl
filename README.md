# repoctl — runtime-only artifact

This archive contains the repoctl command implementation, versioned JSON contracts, source providers, the pinned Python dependency files and an explicit template-document compatibility manifest. It has **no human Task templates, Board, PRD, Handoff scaffold or agent hierarchy**.

Run `./scripts/repoctl --version` for the CLI identity. To operate on external project Tasks, set `REPOCTL_WORKSPACE_ROOT` to the absolute path of a compatible existing template workspace; repoctl never guesses that path from the current directory. A document-only template is independently usable without this program.

An unversioned pre-separation workspace remains in its legacy lane. An explicitly versioned document contract outside the supported 1.x major range fails closed for Task resume. This artifact does not migrate adopters or publish remote repositories.

Release identity: repoctl 0.16.0.
