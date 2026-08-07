package lab.provisioncluster;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.model.VariableProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class ProvisionClusterSpecTest {
    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
    }

    @Test
    public void planExposesTheNameAndConfigVariables() {
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertEquals("lab1", defaultOf(plan, "cluster_name"));
        assertEquals(
                "an empty cluster_config means the config named after the cluster",
                "",
                defaultOf(plan, "cluster_config"));
    }

    @Test
    public void planExposesNoOverrideVariables() {
        // cluster_type and addons now live in the cluster's YAML config, which
        // is the single source of truth: a run selects a config, it does not
        // patch one.
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertTrue(
                "no plan variable may shadow the config",
                plan.getVariables().stream()
                        .noneMatch(v -> "cluster_type".equals(v.getName())
                                || "addons".equals(v.getName())));
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
