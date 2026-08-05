package lab.provisioncluster;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.artifact.Artifact;
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

    // Mirrors forgelab/planvars.py. Both sides must agree or the placeholder
    // stops reading as "unset" and a default run provisions something nobody
    // asked for; ProvisionClusterSpecTest pins the strings.
    static final String PLACEHOLDER_TYPE = "k8s | dcos";
    static final String PLACEHOLDER_ADDONS = "hdfs,keycloak,opensearch (or none)";

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Provision Cluster", new BambooKey("PROV"))
                .description("Terraform+Ansible: provision named multipass cluster")
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME))
                // A plan variable is a key and a value — Bamboo has no field for
                // a hint — so the defaults double as the documentation of what
                // each one accepts. Neither placeholder is a legal value, and
                // leaving one untouched means "no override": the cluster's
                // tfvars wins. Kept in step with forgelab/planvars.py by
                // ProvisionClusterSpecTest.
                .variables(
                        new Variable("cluster_name", "lab1"),
                        new Variable("cluster_type", PLACEHOLDER_TYPE),
                        new Variable("addons", PLACEHOLDER_ADDONS))
                .planBranchManagement(new PlanBranchManagement().delete(
                        new com.atlassian.bamboo.specs.api.builders.plan.branches.BranchCleanup()))
                .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
                .stages(
                        // Deliberately NOT requiring agent.role=host: this stage
                        // needs Python and the checkout, nothing else, so a bad
                        // cluster_type fails in seconds on whichever agent is
                        // free instead of waiting for the host agent to finish
                        // whatever cluster it is building.
                        new Stage("Validate").jobs(
                                new Job("Validate", new BambooKey("JOB0"))
                                        .tasks(
                                        new VcsCheckoutTask().description("checkout")
                                                .checkoutItems(new CheckoutItem().defaultRepository()),
                                        new ScriptTask().description("validate plan variables")
                                                .inlineBody("bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py "
                                                        + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_type}\" "
                                                        + "\"${bamboo.addons}\""))),
                        new Stage("Provision").jobs(
                        new Job("Provision", new BambooKey("JOB1"))
                                // Host-only: multipass/terraform/ansible live on the Mac host
                                // agent. Without this the job can land on the containerized
                                // k8s agent (agent.role=ci), which has none of them.
                                .requirements(new Requirement("agent.role")
                                        .matchValue("host").matchType(Requirement.MatchType.EQUALS))
                                // provision.py writes the cluster's info file into the working
                                // copy; publishing it makes the addresses and component list
                                // readable from the build result, without a checkout.
                                .artifacts(new Artifact().name("cluster-info")
                                        .location("cluster_registered")
                                        .copyPattern("*_cluster_info.yml")
                                        .shared(true)
                                        .required(false))
                                .tasks(
                                new VcsCheckoutTask().description("checkout")
                                        .checkoutItems(new CheckoutItem().defaultRepository()),
                                new ScriptTask().description("provision cluster")
                                        .inlineBody("bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py "
                                                + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_type}\" "
                                                + "\"${bamboo.addons}\""))));
    }

    public static void main(String[] args) {
        new BambooServer(SpecConstants.BAMBOO_URL).publish(new ProvisionClusterSpec().plan());
    }
}
