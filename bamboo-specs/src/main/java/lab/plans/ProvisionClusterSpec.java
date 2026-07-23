package lab.plans;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.configuration.ConcurrentBuilds;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.repository.VcsRepositoryIdentifier;
import com.atlassian.bamboo.specs.api.builders.plan.branches.PlanBranchManagement;
import com.atlassian.bamboo.specs.api.builders.Variable;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.util.BambooServer;

@BambooSpec
public class ProvisionClusterSpec {

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Provision Cluster", new BambooKey("PROV"))
            .description("Terraform+Ansible: provision named multipass cluster")
            .variables(
                new Variable("cluster_name", "lab1"),
                new Variable("cluster_type", ""))
            .planBranchManagement(new PlanBranchManagement().delete(
                new com.atlassian.bamboo.specs.api.builders.plan.branches.BranchCleanup()))
            .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
            .stages(new Stage("Provision").jobs(
                new Job("Provision", new BambooKey("JOB1")).tasks(
                    new VcsCheckoutTask().description("checkout")
                        .checkoutItems(new CheckoutItem().defaultRepository()),
                    new ScriptTask().description("provision cluster")
                        .inlineBody("provisioning/scripts/provision.sh "
                            + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_type}\""))));
    }

    public static void main(String[] args) {
        new BambooServer(HelloWorldSpec.BAMBOO_URL).publish(new ProvisionClusterSpec().plan());
    }
}
