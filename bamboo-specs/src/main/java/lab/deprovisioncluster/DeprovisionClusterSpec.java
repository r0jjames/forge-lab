package lab.deprovisioncluster;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.configuration.ConcurrentBuilds;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.api.builders.repository.VcsRepositoryIdentifier;
import com.atlassian.bamboo.specs.api.builders.plan.branches.PlanBranchManagement;
import com.atlassian.bamboo.specs.api.builders.Variable;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.util.BambooServer;
import lab.shared.SpecConstants;

@BambooSpec
public class DeprovisionClusterSpec {

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Deprovision Cluster", new BambooKey("DEPROV"))
                .description("Destroy named cluster + sweep leftovers")
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME))
                .variables(
                        new Variable("cluster_name", "lab1"))
                .planBranchManagement(new PlanBranchManagement().delete(
                        new com.atlassian.bamboo.specs.api.builders.plan.branches.BranchCleanup()))
                .stages(new Stage("Deprovision").jobs(
                        new Job("Deprovision", new BambooKey("JOB1"))
                                // Host-only, same reason as ProvisionClusterSpec: the
                                // multipass/terraform toolchain exists only on the host agent.
                                .requirements(new Requirement("agent.role")
                                        .matchValue("host").matchType(Requirement.MatchType.EQUALS))
                                .tasks(
                                new VcsCheckoutTask().description("checkout")
                                        .checkoutItems(new CheckoutItem().defaultRepository()),
                                new ScriptTask().description("deprovision cluster")
                                        .inlineBody("bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.sh "
                                                + "\"${bamboo.cluster_name}\""))));
    }

    public static void main(String[] args) {
        new BambooServer(SpecConstants.BAMBOO_URL).publish(new DeprovisionClusterSpec().plan());
    }
}
