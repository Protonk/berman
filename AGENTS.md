# Working on Berman

Start with the [README](README.md) for the producer’s workflow. Berman runs in Linux alongside the project and the actor. The producer chooses when to open an episode and which additional jobs, if any, to give the actor. Each sitting is attended in an ordinary terminal or unattended with a message, and the producer decides when to close. A process exit, missing transcript, or actor’s final message describes a sitting. Only an explicit producer ending closes the episode.

Use *producer*, *actor*, *episode*, *sitting*, and *additional job* consistently in the CLI, records, templates and guides. Write each additional job so an actor can carry it out alongside ordinary work, either on its own or with other selected jobs. Give any requested files distinct locations in the return directory. The producer can ask for notes, a current handover, or both.

Keep the ordinary project path free of Berman instructions and collection. Attribute an observation to a specific episode before writing it. Keep actor returns, observed telemetry, and later interpretations individually identifiable. Use fresh destinations for records and reviews. Never fix a failed verification by rewriting a source.

[Development](docs/development.md) specifies the checks and what they exercise. Use disposable checkouts, synthetic actors and temporary homes for routine tests. A native session needs the producer’s choice of project and actor. Review any changes to shared capture hooks in the actual harness CLI. Keep local credentials and project records outside this software repository.

Commit coherent changes using Git. Push the software and cut releases only at the maintainer’s request. On this Mac, make an authorized push from the guest checkout using its [Git credential helper](development/lima/README.md#push-berman-commits-from-the-guest). Between releases the package keeps the last released version. Record public changes under [unreleased](docs/releases/unreleased.md). Keep the application and its job templates under `src/berman/`, and the local development environments under `development/`.
