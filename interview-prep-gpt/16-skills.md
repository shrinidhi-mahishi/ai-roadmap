# Skills

## Why It Matters
Skills are Deep Agents' reusable workflow and domain-knowledge primitive. They solve a common context problem: some instructions are important, but not important enough to preload into every prompt forever. In interviews, the best description is "progressive-disclosure procedural memory."

That phrase matters because a skill is not just static documentation. It is a package of instructions that the agent can discover, open, and then follow when relevant.

## Mental Model
Each skill is a directory centered on `SKILL.md`:

- frontmatter says what the skill is and when to use it
- the body tells the agent what to do
- supporting files hold scripts, references, or assets loaded only when needed

Deep Agents loads skills in layers:

1. metadata at startup
2. full instructions when the skill is activated
3. supporting resources on demand after activation

That is the progressive-disclosure story.

## Architecture / Flow
```text
skills=["/skills/"]
  -> SkillsMiddleware scans each source directory
  -> parse SKILL.md frontmatter
  -> inject skill name + description into startup prompt
  -> model decides a skill is relevant
  -> read full SKILL.md through read_file
  -> follow instructions
  -> optionally open scripts/, references/, assets/
```

In other words, the agent does not memorize the whole skill library up front. It discovers, then drills down.

## Key Concepts
- A skill is a directory containing `SKILL.md` plus optional supporting resources.

- `SKILL.md` begins with YAML frontmatter. The main fields are:
  - `name`
  - `description`
  - optional `license`
  - optional `compatibility`
  - optional `metadata`
  - optional `allowed-tools`

- `allowed-tools` is experimental. The docs describe it as a space-separated list of pre-approved tools the skill can use.

- Source paths in `skills=` must point to directories that contain skill directories. Pointing directly at the skill directory itself does not load it.

- Supporting resources are explicit, not magical. If `scripts/`, `references/`, or `assets/` matter, `SKILL.md` should say what they contain and when to open them.

- Skills and memory are related but different:
  - memory is always loaded
  - skills are discovered lightly and opened deeply only when relevant

- Subagent inheritance is asymmetric:
  - the auto-added `general-purpose` subagent inherits the main agent's skills
  - custom subagents do not inherit them by default and need their own `skills=` paths
  - skill state is isolated between parent and child

- Skill visibility and mutability are separate controls. You can route which skills exist for which users with backend scoping, then separately deny writes to `/skills/**` or require approval for changes.

## Metrics and Formulas to Memorize
- `3` progressive-disclosure levels:
  - metadata
  - instructions
  - supporting resources
- Frontmatter constraints called out in the docs:
  - `name`: lowercase alphanumeric plus hyphens, `1-64` chars, must match parent directory name
  - `description`: max `1024` chars
- Authoring guidance:
  - keep the `SKILL.md` body under about `5000` tokens
  - keep it under about `500` lines
- Hard loading limit: `SKILL.md` files over `10 MB` are skipped

## Trade-offs and Failure Modes
- Too many overlapping skills reduce selection quality. The model only sees descriptions at discovery time, so fuzzy or repetitive descriptions create ambiguity.

- Passing a direct skill directory path instead of its parent source directory silently prevents discovery.

- Supporting files are not auto-discovered. If `SKILL.md` never references them, the agent may never use them.

- Writable shared skills are a prompt-injection risk. If many users can edit the same skill, they can poison instructions other users later read.

- Custom subagents often "lose" skills because people assume inheritance. The docs are explicit that only the general-purpose subagent inherits the parent's skills.

## Interview Q&A
**Q: What problem do skills solve in Deep Agents?**  
A: They provide reusable workflows and domain knowledge with progressive disclosure, so the agent gets targeted capability without bloating every prompt.

**Q: What does a skill actually look like?**  
A: A directory with a `SKILL.md` file containing YAML frontmatter plus instructions, and optionally scripts, references, or assets.

**Q: What loads at startup?**  
A: Only each skill's metadata, mainly `name` and `description`.

**Q: What is `allowed-tools`?**  
A: An experimental frontmatter field for a space-separated list of pre-approved tools the skill can use.

**Q: Do subagents inherit skills?**  
A: Only the default `general-purpose` subagent does. Custom subagents need their own `skills=` configuration.

**Q: How do you make skills safe in production?**  
A: Scope visibility with backends, make shared skills read-only with permissions, and use interrupts when skill edits need review.

## Sources
- [Skills](https://docs.langchain.com/oss/python/deepagents/skills.md)
- [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
