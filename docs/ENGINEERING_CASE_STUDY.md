# AI Job Seeker â€” Engineering Case Study

## Project Overview

**Project:** AI Job Seeker
**Developer and system owner:** Claud
**Stack:** Python, Flask, MySQL, Ollama, n8n, Docker
**Status:** Personal engineering prototype under active development.

AI Job Seeker is a local-first workflow automation project that brings job discovery, normalization, relevance scoring, application drafting, eligibility checks, and human-reviewed application tracking into one workflow.

It is a personal prototype, not a production SaaS deployment. No production-scale reliability, hiring success, or quantified productivity improvement is claimed.

## Problem

Searching for jobs across multiple platforms involves repeated work: finding listings, reviewing descriptions, comparing skills and location requirements, preparing application material, and tracking application status.

The engineering goal was to consolidate these tasks into a traceable workflow while retaining human control over consequential decisions.

## My Ownership and Contributions

I designed and implemented this project as an independent engineering initiative. My work includes:

- Designing the end-to-end workflow and component architecture.
- Implementing Python job-source adapters and discovery orchestration.
- Normalizing collected listing data and handling duplicates.
- Developing a configurable weighted job-matching pipeline.
- Implementing location interpretation and eligibility review checks.
- Integrating Ollama with Llama 3.2 for assisted application drafting.
- Building MySQL-backed profile, job, match, and application workflows.
- Developing Flask dashboard and API functionality.
- Implementing explicit review actions and guarded application-state transitions.
- Integrating local workflow orchestration with n8n and Docker-based services.
- Writing and debugging automated tests for route behavior, state transitions, query safety, source failure handling, and eligibility gates.

The repository represents work I personally designed, implemented, integrated, and tested. The project evolved iteratively through debugging and refinement.

## System Workflow

1. **Discovery:** Python adapters retrieve listings from supported external job sources.
2. **Normalization:** Source-specific records are converted into a consistent internal representation.
3. **Persistence:** Job data is stored in MySQL for subsequent matching and review.
4. **Matching:** A weighted scoring engine evaluates role relevance, skill compatibility, and location signals.
5. **Prioritization:** Matching results are organized into review and shortlist workflows.
6. **Eligibility review:** Pre-checks identify expired, closed, uncertain, or potentially unsuitable listings.
7. **AI drafting:** Ollama with Llama 3.2 assists in generating application draft content.
8. **Human review:** The Flask dashboard exposes draft review, approval, and archive actions.
9. **Manual submission:** The user submits through the employer's official channel and can then record the confirmed outcome.

See [Architecture, Guardrails, and Evaluation](ARCHITECTURE_AND_EVALUATION.md) for component responsibilities and test coverage.

## Matching Approach

The configured matching dimensions are:

| Dimension | Weight |
|---|---:|
| Role relevance | 40% |
| Skills compatibility | 35% |
| Location compatibility | 25% |

Conceptually:

`Overall Score = (Role Score Ã— 0.40) + (Skills Score Ã— 0.35) + (Location Score Ã— 0.25)`

The weighted model makes prioritization explicit and inspectable. It is a ranking aid, not a guarantee of job suitability, work authorization, or employer acceptance of applicants from a given country.

## AI Integration

The drafting component uses **Ollama** as its local model runtime and **Llama 3.2** as the language model. It generates draft application material using job and applicant context.

The model assists with content preparation; it does not make the final application decision. Generated content must be reviewed and corrected before external use. Model output is not treated as verified fact.

## Application State and Human Oversight

Drafts can be stored with a `pending_approval` status. The review workflow distinguishes between drafts awaiting approval, approved drafts, archived drafts, and submissions explicitly recorded by the user.

Approval is an internal review action. It does not submit an application.

A submission can be recorded only after the user has approved the draft, submitted it through the official external channel, and explicitly confirmed that action in the dashboard. The dashboard does not log into job boards, complete external forms, or click employer submission buttons.

## Evaluation

On November 15, 2026, the portfolio copy passed **32 automated unit tests** using Python's `unittest` framework.

| Test area | Tests |
|---|---:|
| Application action routes | 5 |
| Application queries and state transitions | 10 |
| Himalayas search expansion and failure handling | 3 |
| Eligibility and manual-review gates | 11 |
| Query builder and SQL safety | 3 |
| **Total** | **32** |

The tests cover application transitions, CSRF rejection, profile-scoped queries, parameterized SQL, source failure handling, search pagination and deduplication behavior, deadline checks, and eligibility review conditions.

The suite uses simulated database behavior and controlled network failures where appropriate. Passing unit tests do not establish production-scale reliability or prove that every external source is currently available.

Run the suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

## Reliability and Security Considerations

The implementation includes:

- Parameterized SQL queries.
- Profile-scoped application operations.
- Session-bound CSRF token validation.
- Explicit status-transition checks.
- Duplicate submission timestamp protection.
- Eligibility gates for uncertain or disqualifying conditions.
- Explicit user confirmation before recording an external submission.
- Local-first execution with runtime credentials kept outside the source repository.

External job sources may return incomplete data, change their interfaces, or reject requests. Missing information is treated as uncertainty requiring review, not as proof of eligibility.

## Demonstrated Outcomes and Limitations

**Demonstrated in the current prototype:**

- An integrated local workflow spanning discovery, matching, drafting, and review.
- A database-backed application management interface.
- Human approval and explicit submission-confirmation controls.
- A 32-test automated validation suite passing locally.

**Not yet demonstrated:**

- Production-scale uptime, throughput, or operational reliability.
- Independently measured time savings or matching precision.
- Application conversion or hiring outcomes.
- Comprehensive live-source availability.
- Production security certification or independent penetration testing.

The project remains a personal prototype under active development. Future work includes stronger matching evaluation, improved source reliability, more robust location and work-authorization evidence, expanded observability, and more reproducible deployment.