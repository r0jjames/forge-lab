# agent-image (AGENT-BUILD)

Builds and pushes the containerized Bamboo CI agent image with kaniko.

- **Spec:** `bamboo-specs/src/main/java/lab/agentimage/BuildAgentImageSpec.java`
- **Script:** *not in this repo.* The plan checks out
  [bamboo-agent](https://github.com/r0jjames/bamboo-agent) as a plan-local
  repository and runs `bamboo-agent-deployment/scripts/build-image.sh` from
  there, alongside the Dockerfile, VERSION file, and kaniko template it needs.

That repo owns the image; this repo owns only the pipeline that builds it, so
this directory holds no scripts. Anything added here must be something *this*
plan runs and that does not belong with the image sources.

The job requires the `agent.role=ci` capability, so it stays queued until the
containerized agent is deployed and approved — the host agent
(`agent.role=host`) never picks it up.
