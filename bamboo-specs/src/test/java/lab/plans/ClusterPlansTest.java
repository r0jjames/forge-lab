package lab.plans;

import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class ClusterPlansTest {
    @Test
    public void provisionPlanIsValid() {
        EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
    }

    @Test
    public void deprovisionPlanIsValid() {
        EntityPropertiesBuilders.build(new DeprovisionClusterSpec().plan());
    }
}
