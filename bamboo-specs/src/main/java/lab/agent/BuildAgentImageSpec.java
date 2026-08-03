package lab.agent;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.configuration.ConcurrentBuilds;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.builders.repository.git.GitRepository;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.util.BambooServer;

/**
 * Build + push plan for the bamboo-agent container image. The image sources
 * (Dockerfile, VERSION, kaniko template, build script) live in the separate
 * bamboo-agent repo, which this plan checks out; only the pipeline definition
 * lives here, alongside the rest of the lab's plans-as-code.
 */
@BambooSpec
public class BuildAgentImageSpec {

    // Bamboo server reached over the `make ui` port-forward at publish time.
    static final String BAMBOO_URL = "http://localhost:8085";

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("AGENT")).name("bamboo-agent"),
                "Build Agent Image", new BambooKey("BUILD"))
                .description("Build + push the containerized Bamboo CI agent via kaniko")
                .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
                // Plan-local repo: no pre-created linked repository needed. Public
                // repo over https, so no credentials.
                .planRepositories(
                        new GitRepository()
                                .name("bamboo-agent")
                                .url("https://github.com/r0jjames/bamboo-agent.git")
                                .branch("main"))
                .stages(
                        new Stage("Build+Push").jobs(
                                new Job("BuildPush", new BambooKey("BP"))
                                        .requirements(new Requirement("agent.role").matchValue("ci").matchType(Requirement.MatchType.EQUALS))
                                        .tasks(
                                                new VcsCheckoutTask().description("checkout")
                                                        .checkoutItems(new CheckoutItem().defaultRepository()),
                                                new ScriptTask().description("kaniko build + push")
                                                        .inlineBody("bamboo-agent-deployment/scripts/build-image.sh"))));
    }

    public static void main(String[] args) {
        BambooServer server = new BambooServer(BAMBOO_URL);
        server.publish(new BuildAgentImageSpec().plan());
    }
}
