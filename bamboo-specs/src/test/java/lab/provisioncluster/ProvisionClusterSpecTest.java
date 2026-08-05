package lab.provisioncluster;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.model.VariableProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.Test;

public class ProvisionClusterSpecTest {
    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
    }

    @Test
    public void planExposesTheAddonsVariable() {
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertTrue(
                "the addons plan variable is how a build overrides the cluster's tfvars",
                plan.getVariables().stream().anyMatch(v -> "addons".equals(v.getName())));
    }

    @Test
    public void variableDefaultsAreThePlaceholders() {
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertEquals(ProvisionClusterSpec.PLACEHOLDER_TYPE, defaultOf(plan, "cluster_type"));
        assertEquals(ProvisionClusterSpec.PLACEHOLDER_ADDONS, defaultOf(plan, "addons"));
    }

    @Test
    public void placeholdersMatchPlanvars() throws Exception {
        // planvars.py treats these exact strings as "no override". A drift here
        // makes every default run silently provision the tfvars cluster while
        // the dialog claims otherwise, so pin the two sides together.
        Path planvars = Path.of(
                "src/main/java/lab/shared/python/forgelab/planvars.py");
        List<String> lines = Files.readAllLines(planvars);
        assertTrue(
                "planvars.PLACEHOLDER_TYPE must equal the spec's",
                lines.contains(
                        "PLACEHOLDER_TYPE = \"" + ProvisionClusterSpec.PLACEHOLDER_TYPE + "\""));
        assertTrue(
                "planvars.PLACEHOLDER_ADDONS must equal the spec's",
                lines.contains(
                        "PLACEHOLDER_ADDONS = \"" + ProvisionClusterSpec.PLACEHOLDER_ADDONS + "\""));
    }

    @Test
    public void validateStageRunsBeforeProvisionAndOnAnyAgent() {
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertEquals("Validate", plan.getStages().get(0).getName());
        assertEquals("Provision", plan.getStages().get(1).getName());
        assertTrue(
                "Validate must not require agent.role=host — that is the point of it",
                plan.getStages().get(0).getJobs().get(0).getRequirements().isEmpty());
    }

    private static String defaultOf(PlanProperties plan, String name) {
        return plan.getVariables().stream()
                .filter(v -> name.equals(v.getName()))
                .map(VariableProperties::getValue)
                .findFirst()
                .orElseThrow(() -> new AssertionError("no plan variable " + name));
    }
}
