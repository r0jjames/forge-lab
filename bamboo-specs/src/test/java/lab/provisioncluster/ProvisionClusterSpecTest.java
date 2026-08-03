package lab.provisioncluster;

import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class ProvisionClusterSpecTest {
    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
    }
}
