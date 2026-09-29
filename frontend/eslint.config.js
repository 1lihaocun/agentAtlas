import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import hooks from 'eslint-plugin-react-hooks';

const features = ['assets', 'instructions', 'memories', 'files', 'duplicates', 'search', 'reviews', 'file-guide', 'settings', 'jobs'];
export default tseslint.config(
  { ignores: ['dist/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': hooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'no-restricted-syntax': ['error', {
        selector: "JSXOpeningElement[name.name='select']",
        message: '下拉框统一使用 shared/ui/FilterDropdown，保留滚动和尺寸调节功能。',
      }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  ...features.map(feature => ({
    files: [`src/features/${feature}/**/*.{ts,tsx}`],
    rules: { 'no-restricted-imports': ['error', { patterns: [
      { group: ['**/app/**'], message: '功能模块通过参数和回调与应用协作。' },
      ...features.filter(other => other !== feature).map(other => ({
        group: [`**/${other}/**`], message: '跨功能组合放在 app 目录。',
      })),
    ] }] },
  })),
  {
    files: ['src/shared/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': ['error', { patterns: ['**/features/**', '**/app/**'] }] },
  },
);
