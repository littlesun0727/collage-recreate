/** Keep pixel-coordinate analysis off the SDK's implicit local_image path. */
export const IMAGE_TRANSPORT = 'tool-original-v1';

export function originalImageInstructions(paths) {
  if (!paths.length) return '';
  const calls = paths.map((p, i) => `const picture${i} = await tools.view_image({path: ${JSON.stringify(p)}, detail: "original"});\nimage(picture${i}.image_url, "original");`).join('\n');
  return `图片通过下列本地路径提供，不附加默认 local_image。开始视觉分析前，使用 functions.exec 原样执行以下看图代码，按顺序打开图片（第一张是参考图）：\n\n${calls}\n\n后续查看叠框或其他图片时，也使用 view_image 的 detail:"original" 并在 image 转发时显式传 "original"。不要只转发 image_url 而省略第二个参数。不得自行缩放图片；bbox 使用任务声明的原图像素画布。`;
}

export function originalImageInput(prompt, paths) {
  return [{type: 'text', text: prompt + '\n\n' + originalImageInstructions(paths)}];
}
