Pod::Spec.new do |s|
  s.name = 'LightworkTransport'
  s.version = '0.1.0'
  s.summary = 'Bounded private Lightwork companion requests'
  s.description = s.summary
  s.license = { :type => 'Proprietary' }
  s.author = 'Daybreak AI Labs'
  s.homepage = 'https://github.com/Daybreak-AI-Labs/Lightwork'
  s.source = { :git => 'https://github.com/Daybreak-AI-Labs/Lightwork.git' }
  s.platforms = { :ios => '15.1' }
  s.swift_version = '5.9'
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.source_files = '**/*.swift'
  s.pod_target_xcconfig = { 'DEFINES_MODULE' => 'YES' }
end
