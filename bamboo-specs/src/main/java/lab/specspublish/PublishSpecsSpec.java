package lab.specspublish;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.configuration.ConcurrentBuilds;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.repository.VcsRepositoryIdentifier;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.builders.trigger.RepositoryPollingTrigger;
import com.atlassian.bamboo.specs.util.BambooServer;
import java.time.Duration;
import lab.shared.SpecConstants;

/**
 * Republishes every plan in this repo whenever main changes.
 *
 * <p>The pre-push hook covers pushes from the lab's own clone; this plan covers
 * everything else — another machine, a merge in the GitHub UI, a push made while
 * the lab was switched off. Polling rather than a webhook because GitHub cannot
 * reach a Bamboo that only exists behind a local port-forward.
 *
 * <p>This spec is discovered by publish_specs.py like any other, so the plan
 * republishes itself. A change here therefore takes effect on the run after the
 * one that publishes it. That is not a bug.
 */
@BambooSpec
public class PublishSpecsSpec {

    /** Long enough to stay quiet, short enough that a merge lands while you watch. */
    static final Duration POLL_PERIOD = Duration.ofMinutes(3);

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Publish Specs", new BambooKey("SPECS"))
                .description("Republish every Bamboo Specs plan from main")
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME))
                .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
                .triggers(new RepositoryPollingTrigger().withPollingPeriod(POLL_PERIOD))
                .stages(
                        new Stage("Publish").jobs(
                                new Job("Publish", new BambooKey("PUB"))
                                        // Host-only: localhost:8085 is Bamboo only through the
                                        // `make ui` port-forward, and the PAT is a host file.
                                        .requirements(new Requirement("agent.role")
                                                .matchValue("host")
                                                .matchType(Requirement.MatchType.EQUALS))
                                        .tasks(
                                                new VcsCheckoutTask().description("checkout")
                                                        .checkoutItems(new CheckoutItem().defaultRepository()),
                                                new ScriptTask().description("publish specs")
                                                        .inlineBody("bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py"))));
    }

    public static void main(String[] args) {
        BambooServer server = new BambooServer(SpecConstants.BAMBOO_URL);
        server.publish(new PublishSpecsSpec().plan());
    }
}
