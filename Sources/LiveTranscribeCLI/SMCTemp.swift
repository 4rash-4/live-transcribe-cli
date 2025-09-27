//
//  SMCTemp.swift
//  LiveTranscribeCLI
//
//  Reads M1/Intel package temperature via IOKit (public API).
//

import Foundation
import IOKit

/// Returns package temperature in °C, or 0 if sensor unavailable.
func readPackageTemp() -> Double {
    let service = IOServiceGetMatchingService(kIOMainPortDefault,
                                              IOServiceMatching("IOHIDSystem"))
    guard service != 0 else { return 0 }
    var temp: Double = 0
    if let sensor = IORegistryEntryCreateCFProperty(service, "temperature" as CFString,
                                                    kCFAllocatorDefault, 0) {
        temp = (sensor as? NSNumber)?.doubleValue ?? 0
    }
    IOObjectRelease(service)
    return temp
}