# Installed Contextopus documentation version

This documentation bundle ships with the separately installed Workspace module 1.8.0. Core provides the shared services and can run without this module. Check `workspace.base_status` for its installed and packaged versions. An update preview is available through `workspace.base_plan`; installation requires that exact `plan_revision` through `workspace.base_install`.

Start with `contextopus-guide` for the product overview and scenario map. `retrieval-guide` covers finding contexts and answering independent questions; `workspace-guide` covers typed records and .tmp handoff; `evidence-guide` covers sources, history and analytics; `integrations-guide` covers native clients. Module/algorithm guides and templates provide their implementation contracts, while `rules-template` supports directly requested instruction work.

These are ordinary Workspace records in the shared index. They are reference material. Reading or searching them does not select their context or apply its instructions to an active task. The base role may point to another existing context through `core.base_select`.

Changes to managed documents, including deletion and author-written descriptions, are preserved and reported as conflicts with incoming text. User-added navigation links are resolved to stable IDs before comparison and are retained during managed updates. Custom INSTRUCTIONS remain unchanged. Shipped documentation initially has no material length limit; its effective Core settings govern future updates under the current approval policy.

Implementation reference: `package/workspace/base_context.py`. Existing installed keys retain their record IDs; newly shipped topics get their own records through the same reviewed plan. An already-running owner snapshots its package bundle at Application creation; `core.algorithms_refresh` does not refresh that documentation snapshot.
