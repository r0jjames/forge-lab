package lab.plans;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.util.BambooServer;

@BambooSpec
public class HelloWorldSpec {

    static final String BAMBOO_URL = "http://localhost:8085";

    /**
     * Linked repository the cluster plans check out; created once in Administration > Linked repositories.
     */
    static final String REPO_NAME = "forge-lab";

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Hello World", new BambooKey("HELLO"))
                .description("Proves Specs-to-server publish loop")
                .stages(new Stage("Default").jobs(
                        new Job("Say hello", new BambooKey("JOB1")).tasks(
                                new ScriptTask().description("hello")
                                        .inlineBody("echo hello from forge-lab"))));
    }

    public static void main(String[] args) {
        BambooServer server = new BambooServer(BAMBOO_URL);
        server.publish(new HelloWorldSpec().plan());
    }
}
