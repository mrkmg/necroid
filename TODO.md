# TODO

1. Add groups for mods - e.g. "Utility", "Fun", "Admin", etc.
2. Add support for dependent mods. Should provide layered diff applications. For dev: add the dependency. When you enter, it first applies the dependencies, then applies the mod itself. Capturing will capture against pristine + dependents, so the diff of a mod will not include the changes from its dependencies, but the changes from the dependencies to the current state. Applying the mod will include the dependencies. This allows for better modularity and reuse of mods. Need to be smart and make sure changes are applied in the correct order when installing.
3. Add support for project zomboid versions. Mods should be able to support multiple versions, with auto-detection of the version installed and auto-installation of the correct version of the mod.
4. Add example .mod-config.json to readme.

## Ideas - Possible Enhancements

- Create full integration with workshop and mod loading inside project zomboid.
- Create support for companion steam workshop mod recommendations.