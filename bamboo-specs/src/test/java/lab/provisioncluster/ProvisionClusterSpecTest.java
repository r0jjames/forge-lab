package lab.provisioncluster;

import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
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
}
