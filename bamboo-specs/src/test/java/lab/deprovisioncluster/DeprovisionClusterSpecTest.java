package lab.deprovisioncluster;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class DeprovisionClusterSpecTest {
    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new DeprovisionClusterSpec().plan());
    }

    @Test
    public void validateStageRunsBeforeDeprovisionAndOnAnyAgent() {
        PlanProperties plan = EntityPropertiesBuilders.build(new DeprovisionClusterSpec().plan());
        assertEquals("Validate", plan.getStages().get(0).getName());
        assertEquals("Deprovision", plan.getStages().get(1).getName());
        assertTrue(
                "Validate must not require agent.role=host — that is the point of it",
                plan.getStages().get(0).getJobs().get(0).getRequirements().isEmpty());
    }
}
