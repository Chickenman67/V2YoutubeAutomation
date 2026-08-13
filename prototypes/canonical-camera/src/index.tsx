import { Composition, registerRoot } from "remotion";
import { CameraExcursion } from "./CameraExcursion";

// Prototype: 1920x1080, 30fps, 10s. Camera archetype = hero -> zoom into tile -> return -> offset.
const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CameraExcursion"
      component={CameraExcursion}
      durationInFrames={330}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
export default RemotionRoot;

registerRoot(RemotionRoot);

