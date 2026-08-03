package lab.provisioncluster;

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
public class ProvisionClusterSpec {

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Provision Cluster", new BambooKey("PROV"))
                .description("Terraform+Ansible: provision named multipass cluster")
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME))
                .variables(
                        new Variable("cluster_name", "lab1"),
                        new Variable("cluster_type", ""))
                .planBranchManagement(new PlanBranchManagement().delete(
                        new com.atlassian.bamboo.specs.api.builders.plan.branches.BranchCleanup()))
                .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
                .stages(new Stage("Provision").jobs(
                        new Job("Provision", new BambooKey("JOB1"))
                                // Host-only: multipass/terraform/ansible live on the Mac host
                                // agent. Without this the job can land on the containerized
                                // k8s agent (agent.role=ci), which has none of them.
                                .requirements(new Requirement("agent.role")
                                        .matchValue("host").matchType(Requirement.MatchType.EQUALS))
                                .tasks(
                                new VcsCheckoutTask().description("checkout")
                                        .checkoutItems(new CheckoutItem().defaultRepository()),
                                new ScriptTask().description("provision cluster")
                                        .inlineBody("plans/provision-cluster/scripts/provision.sh "
                                                + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_type}\""))));
    }

    public static void main(String[] args) {
        new BambooServer(SpecConstants.BAMBOO_URL).publish(new ProvisionClusterSpec().plan());
    }
}
