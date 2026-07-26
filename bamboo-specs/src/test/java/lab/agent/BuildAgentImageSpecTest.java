package lab.agent;

import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class BuildAgentImageSpecTest {

    @Test
    public void planIsOfflineValid() {
        Plan plan = new BuildAgentImageSpec().plan();
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(plan);
    }
}
