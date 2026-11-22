# Working on Berman

Start with the [README](README.md) for the manager’s workflow. Berman runs in Linux alongside the project and the actor. The manager chooses when to open an episode, supplies the additional job, starts sittings in an ordinary terminal, and decides when to close. A process exit, missing transcript, or actor’s final message describes a sitting. Only an explicit manager ending closes the episode.

Use *actor*, *episode*, *sitting*, and *additional job* consistently in the CLI, records, templates, examples, and guides. An additional job should work alongside whatever the actor is already doing. Keep the optional note-taking assignment available through the installed package.

Keep the ordinary project path free of Berman instructions and collection. Attribute an observation to a specific episode before writing it. Keep actor returns, observed telemetry, and later interpretations individually identifiable. Use fresh destinations for records and reviews. Never fix a failed verification by rewriting a source.

[Development](docs/development.md) specifies the checks and what they exercise. Use disposable checkouts, synthetic actors and temporary homes for routine tests. A native session needs the manager’s choice of project and actor. Review any changes to shared capture hooks in the actual harness CLI. Keep local credentials and project records outside this software repository.

Commit coherent changes using Git. Push the software and cut releases only at the maintainer’s request. Between releases the package keeps the last released version. Record public changes under [unreleased](docs/releases/unreleased.md). Keep the application, local development apparatus and examples under their respective owners.
